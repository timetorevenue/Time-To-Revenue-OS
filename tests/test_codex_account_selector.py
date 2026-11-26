"""Two-account Codex selection: fixture-only contract tests.

No real model CLI or provider is ever contacted. Every Codex process is a stand-in
script under the temp dir (allowed by the root conftest paid-model guard); each
fixture Codex home holds its own ``stand_in.json`` (account state and usage windows)
and records every launch it receives, so a test can prove which CODEX_HOME a real
launcher passed to its subprocess.

Revisit: when the Codex account contract, ModelPort, the job runner or the website
worker launch changes. · Last touched: 2026-11-16.
"""

from __future__ import annotations

import functools
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import aos_codex_policy as policy  # noqa: E402
import aos_david  # noqa: E402
import aos_jobs  # noqa: E402
import aos_modelport as modelport  # noqa: E402
from aos_agents import factory  # noqa: E402
from aos_agents.runtime import RunEnv, run_agent  # noqa: E402

SCHEMA = {"type": "object", "required": ["category", "summary"],
          "properties": {"category": {"type": "string"}, "summary": {"type": "string"}}}
NOW = 1_800_000_000.0
BILLED = ("OPENAI_API_KEY", "CODEX_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_BASE_URL")

STAND_IN = textwrap.dedent('''\
    #!/usr/bin/env python3
    import json, os, sys, time
    home = os.environ.get("CODEX_HOME", "")
    try:
        conf = json.load(open(os.path.join(home, "stand_in.json")))
    except Exception:
        conf = {}
    argv = sys.argv[1:]
    billed = [k for k in ("OPENAI_API_KEY", "CODEX_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_BASE_URL") if os.environ.get(k)]
    if os.path.isdir(home):
        with open(os.path.join(home, "launches.jsonl"), "a") as handle:
            handle.write(json.dumps({"argv": argv, "codex_home": home, "billed": billed}) + "\\n")
    answer = json.dumps(conf.get("answer") or {"category": "sales", "summary": "ok"})
    def send(obj):
        sys.stdout.write(json.dumps(obj) + "\\n"); sys.stdout.flush()
    if "app-server" in argv:
        for raw in sys.stdin:
            message = json.loads(raw)
            method, ident = message.get("method"), message.get("id")
            if method == "initialize":
                send({"id": ident, "result": {"userAgent": "stand-in"}})
            elif method == "account/read":
                kind = conf.get("account", "chatgpt")
                if kind == "error":
                    send({"id": ident, "error": {"code": -32000, "message": "401 Unauthorized: refresh token expired, please log in again"}})
                elif kind == "none":
                    send({"id": ident, "result": {"account": None, "requiresOpenaiAuth": True}})
                elif kind == "apikey":
                    send({"id": ident, "result": {"account": {"type": "apiKey"}, "requiresOpenaiAuth": True}})
                else:
                    send({"id": ident, "result": {"account": {"type": "chatgpt", "email": "private@example.invalid",
                                                              "planType": "plus"}, "requiresOpenaiAuth": True}})
            elif method == "account/rateLimits/read":
                if conf.get("hang"):
                    time.sleep(30)
                send({"id": ident, "result": conf.get("limits") or {}})
            elif method == "thread/start":
                send({"id": ident, "result": {"thread": {"id": "thr_" + os.path.basename(home)}}})
            elif method == "turn/start":
                send({"id": ident, "result": {"turn": {"id": "turn_1", "status": "inProgress"}}})
                send({"method": "item/completed", "params": {"item": {"type": "agentMessage", "id": "m1", "text": answer}}})
                send({"method": "thread/tokenUsage/updated", "params": {"tokenUsage": {"total": {
                      "inputTokens": 900, "cachedInputTokens": 600, "outputTokens": 40}}}})
                send({"method": "turn/completed", "params": {"turn": {"id": "turn_1", "status": "completed"}}})
        sys.exit(0)
    sys.stdin.read()
    if conf.get("mode") == "quota":
        sys.stderr.write("ERROR: You've hit your usage limit. Try again later.\\n")
        sys.exit(1)
    if conf.get("mode") in ("post_launch_quota", "post_launch_auth"):
        # A provider stop AFTER the session started (an INJECTED fault: this process is a stand-in).
        execs = sum(1 for line in open(os.path.join(home, "launches.jsonl")) if "exec" in json.loads(line)["argv"])
        if execs >= int(conf.get("fail_from", 1)):
            print(json.dumps({"type": "thread.started", "thread_id": "thr_exec"})); sys.stdout.flush()
            message = ("You've hit your usage limit. Try again later." if conf["mode"] == "post_launch_quota"
                       else "401 Unauthorized: refresh token expired, please log in again")
            print(json.dumps({"type": "turn.failed", "error": {"message": message}}))
            sys.exit(1)
    print(json.dumps({"type": "thread.started", "thread_id": "thr_exec"}))
    print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": answer}}))
    print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1200, "cached_input_tokens": 800,
                      "output_tokens": 50, "reasoning_output_tokens": 0}}))
    if "--output-last-message" in argv:
        open(argv[argv.index("--output-last-message") + 1], "w").write(answer)
''')


def limits(five_used: int = 10, weekly_used: int = 10, *, now: float = NOW, five_reset: float | None = None,
           weekly_reset: float | None = None, **flags) -> dict:
    """A provider-shaped account/rateLimits/read result (with identity fields that must not persist)."""
    body = {
        "limitId": "codex", "planType": "plus", "accountId": "acct-private-id",
        "primary": {"usedPercent": five_used, "windowDurationMins": 300,
                    "resetsAt": int(five_reset if five_reset is not None else now + 3600)},
        "secondary": {"usedPercent": weekly_used, "windowDurationMins": 10080,
                      "resetsAt": int(weekly_reset if weekly_reset is not None else now + 86400)},
        "credits": {"hasCredits": False, "unlimited": False, "balance": "0"},
        "spendControlReached": flags.get("spend_control", False),
        "rateLimitReachedType": flags.get("reached"),
    }
    return {"ordinaryUsageAllowed": flags.get("ordinary", True), "rateLimits": body,
            "rateLimitsByLimitId": {"codex": body}, "email": "private@example.invalid"}


def chatgpt(**_):
    return {"account": {"type": "chatgpt", "email": "private@example.invalid", "planType": "plus"}, "requiresOpenaiAuth": True}


class Clock:
    def __init__(self, now: float = NOW):
        self.now = now

    def __call__(self) -> float:
        return self.now


class FakeReader:
    """Provider reads per Codex home, with a call log (a probe is a process start in production)."""

    def __init__(self, responses: dict):
        self.responses = responses       # home name -> dict | Exception | callable
        self.calls: list[str] = []
        self.lock = threading.Lock()
        self.delay = 0.0

    def __call__(self, target):
        with self.lock:
            self.calls.append(target.account)
        if self.delay:
            time.sleep(self.delay)
        value = self.responses[target.account]
        value = value() if callable(value) else value
        if isinstance(value, Exception):
            raise value
        return value


class SelectorCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="aos-accounts-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.cli = self.tmp / "codex"
        self.cli.write_text(STAND_IN, encoding="utf-8")
        self.cli.chmod(self.cli.stat().st_mode | stat.S_IXUSR)
        self.homes = {slot: self.tmp / f"home-{slot.lower()}" for slot in "AB"}
        for home in self.homes.values():
            home.mkdir()
        self.state = self.tmp / "state"
        self.config = policy.AccountConfig(tuple(policy.CodexAccount(s, h) for s, h in self.homes.items()), 20, 60)
        self.base = replace(policy.CODEX_TARGET, executable=self.cli, linux_user=policy.effective_linux_user())
        self.clock = Clock()

    def configure(self, slot: str, **conf) -> None:
        (self.homes[slot] / "stand_in.json").write_text(json.dumps(conf), encoding="utf-8")

    def launches(self, slot: str) -> list[dict]:
        path = self.homes[slot] / "launches.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def select(self, reader=None, **kwargs):
        return policy.select_codex_account(config=self.config, reader=reader, state_dir=self.state, base=self.base,
                                           clock=self.clock, **kwargs)

    def selector(self, reader=None):
        return functools.partial(self.select, reader)

    def reader(self, a=None, b=None) -> FakeReader:
        def one(value):
            if isinstance(value, Exception) or callable(value):
                return value
            return {"account": chatgpt(), "rate_limits": value}
        return FakeReader({"A": one(a if a is not None else limits()), "B": one(b if b is not None else limits())})


# -- the selection rule ------------------------------------------------------------

class SelectionRuleTest(SelectorCase):
    def test_a_healthy_stays_on_a_and_b_is_never_probed(self):
        reader = self.reader(a=limits(50, 70))
        target = self.select(reader)
        self.assertEqual((target.account, target.codex_home), ("A", self.homes["A"]))
        self.assertEqual(reader.calls, ["A"])
        record = target.account_record()
        self.assertEqual(record["slot"], "A")
        self.assertEqual((record["threshold_remaining_percent"], record["threshold_window"]), (20, "five_hour"))
        self.assertEqual([(w["window"], w["remaining_percent"]) for w in record["windows"]], [("five_hour", 50), ("weekly", 30)])
        self.assertIn("A eligible", record["reason"])
        self.assertTrue(record["checked_at"].endswith("Z"))

    def test_threshold_boundary_21_stays_20_and_19_switch_to_b(self):
        for used, expected in ((79, "A"), (80, "B"), (81, "B")):
            with self.subTest(remaining=100 - used):
                shutil.rmtree(self.state, ignore_errors=True)
                target = self.select(self.reader(a=limits(used, 10), b=limits(30, 30)))
                self.assertEqual(target.account, expected)
                if expected == "B":
                    self.assertIn(f"five_hour at {100 - used}% <= 20%", target.selection.reason)
                    self.assertIn("B eligible", target.selection.reason)

    def test_weekly_exhaustion_switches_even_with_five_hour_capacity(self):
        target = self.select(self.reader(a=limits(5, 100), b=limits(10, 10)))
        self.assertEqual(target.account, "B")
        self.assertIn("A not eligible (exhausted", target.selection.reason)
        self.assertIn("weekly exhausted at 0%", target.selection.reason)

    def test_provider_restriction_flags_make_an_account_ineligible(self):
        for flags in ({"reached": "rate_limit_reached"}, {"ordinary": False}, {"spend_control": True}):
            with self.subTest(flags=flags):
                shutil.rmtree(self.state, ignore_errors=True)
                target = self.select(self.reader(a=limits(1, 1, **flags)))
                self.assertEqual(target.account, "B")
                self.assertIn("restricted", target.selection.reason)

    def test_b_must_be_verified_eligible_when_a_is_below_threshold(self):
        low_a = limits(85, 10)
        bad_b = {
            "missing home / no login": RuntimeError("app-server closed its output"),
            "expired auth": RuntimeError("account/read failed: 401 Unauthorized: refresh token expired"),
            "unauthenticated": {"account": {"account": None, "requiresOpenaiAuth": True}, "rate_limits": None},
            "api-key login": {"account": {"account": {"type": "apiKey"}}, "rate_limits": None},
            "stale (window already reset)": {"account": chatgpt(), "rate_limits": limits(10, 10, five_reset=NOW - 5)},
            "malformed percent": {"account": chatgpt(), "rate_limits": {"rateLimits": {"primary": {"usedPercent": "ten"}}}},
            "no windows": {"account": chatgpt(), "rate_limits": {"rateLimits": {}}},
            "B five-hour below threshold too": {"account": chatgpt(), "rate_limits": limits(90, 10)},
            "B weekly exhausted": {"account": chatgpt(), "rate_limits": limits(10, 100)},
            "B no five-hour window": {"account": chatgpt(), "rate_limits": {"rateLimits": {
                "secondary": {"usedPercent": 10, "windowDurationMins": 10080, "resetsAt": int(NOW + 86400)}}}},
        }
        for name, value in bad_b.items():
            with self.subTest(b=name):
                shutil.rmtree(self.state, ignore_errors=True)
                reader = FakeReader({"A": {"account": chatgpt(), "rate_limits": low_a}, "B": value})
                with self.assertRaises(policy.AccountUnavailable) as caught:
                    self.select(reader)
                refusal = caught.exception
                self.assertEqual(refusal.failure_class, "quota_unavailable")   # A's quota is the binding reason
                states = {row["slot"]: row["state"] for row in refusal.accounts}
                self.assertEqual(states["A"], "below_threshold")
                self.assertNotEqual(states["B"], "eligible")
                self.assertIn("no Codex account is eligible", refusal.detail)

    def test_both_needing_sign_in_is_needs_authentication(self):
        reader = FakeReader({"A": RuntimeError("401 unauthorized"), "B": {"account": {"account": None}, "rate_limits": None}})
        with self.assertRaises(policy.AccountUnavailable) as caught:
            self.select(reader)
        self.assertEqual(caught.exception.failure_class, "needs_authentication")
        self.assertGreaterEqual(caught.exception.recheck_after, NOW + policy.AUTH_RECHECK_SECONDS)

    def test_both_unavailable_names_the_earliest_useful_recheck(self):
        reader = self.reader(a=limits(90, 10, five_reset=NOW + 900), b=limits(10, 100, weekly_reset=NOW + 7200))
        with self.assertRaises(policy.AccountUnavailable) as caught:
            self.select(reader)
        self.assertEqual(caught.exception.failure_class, "quota_unavailable")
        self.assertEqual(caught.exception.recheck_after, NOW + 900)
        self.assertEqual(caught.exception.record()["recheck_after"], policy._iso(NOW + 900))

    def test_capacity_reset_on_a_does_not_pull_new_sessions_back_from_b(self):
        state = {"a": limits(85, 10, five_reset=NOW + 600)}
        reader = FakeReader({"A": lambda: {"account": chatgpt(), "rate_limits": state["a"]},
                             "B": {"account": chatgpt(), "rate_limits": limits(10, 10)}})
        self.assertEqual(self.select(reader).account, "B")
        self.clock.now = NOW + 30
        self.assertEqual(self.select(reader).account, "B")           # cached, inside the recheck interval
        self.clock.now = NOW + 601                                   # A's five-hour window has reset
        state["a"] = limits(0, 11, now=self.clock.now)
        target = self.select(reader)
        self.assertEqual(target.account, "B")                        # sticky: B stays active while eligible
        self.assertIn("B active and eligible", target.selection.reason)
        self.assertEqual(reader.calls.count("A"), 1)                 # A is not even re-read

    def test_snapshots_hold_no_identity_or_secret(self):
        self.select(self.reader(a=limits(10, 10)))
        text = (self.state / "A.json").read_text()
        for secret in ("private@example.invalid", "acct-private-id", "token", "email"):
            self.assertNotIn(secret, text)
        saved = json.loads(text)
        self.assertEqual(saved["snapshot"]["account_type"], "chatgpt")
        self.assertEqual(saved["codex_home"], str(self.homes["A"]))


class RecheckAndConcurrencyTest(SelectorCase):
    def test_bounded_rechecks_reuse_the_snapshot_and_do_not_storm_on_failure(self):
        reader = self.reader(a=limits(10, 10))
        for _ in range(5):
            self.select(reader)
        self.assertEqual(reader.calls, ["A"])
        self.clock.now = NOW + 61
        self.select(reader)
        self.assertEqual(reader.calls, ["A", "A"])
        failing = FakeReader({"A": RuntimeError("app-server did not answer"), "B": RuntimeError("app-server did not answer")})
        shutil.rmtree(self.state)
        for _ in range(4):
            with self.assertRaises(policy.AccountUnavailable):
                self.select(failing)
        self.assertEqual(failing.calls, ["A", "B"])                  # one failed read per account per interval

    def test_a_snapshot_whose_window_has_since_reset_is_reread(self):
        reader = self.reader(a=limits(85, 10, five_reset=NOW + 20), b=limits(90, 10))
        with self.assertRaises(policy.AccountUnavailable):
            self.select(reader)
        self.clock.now = NOW + 25                                    # inside 60s, but A's window reset
        reader.responses["A"] = {"account": chatgpt(), "rate_limits": limits(0, 10, now=self.clock.now)}
        self.assertEqual(self.select(reader).account, "A")
        self.assertEqual(reader.calls.count("A"), 2)

    def test_concurrent_launches_probe_once_and_agree(self):
        reader = self.reader(a=limits(90, 10), b=limits(10, 10))
        reader.delay = 0.2
        results, errors = [], []

        def launch():
            try:
                results.append(self.select(reader).account)
            except Exception as exc:  # pragma: no cover - reported below
                errors.append(exc)

        threads = [threading.Thread(target=launch) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertEqual(errors, [])
        self.assertEqual(results, ["B"] * 8)
        self.assertEqual(sorted(reader.calls), ["A", "B"])


class AccountPinTest(SelectorCase):
    def test_in_flight_job_keeps_its_account_when_the_active_account_switches(self):
        state = {"a": limits(90, 10), "b": limits(10, 10)}
        reader = FakeReader({"A": lambda: {"account": chatgpt(), "rate_limits": state["a"]},
                             "B": lambda: {"account": chatgpt(), "rate_limits": state["b"]}})
        running = policy.AccountPin(self.selector(reader))
        self.assertEqual(running.target().account, "B")
        state["a"] = limits(0, 0)
        self.clock.now = NOW + 120
        self.assertEqual(running.target().account, "B")                    # never switched mid-job
        self.assertEqual(policy.AccountPin(self.selector(reader)).target().account, "B")   # new job: sticky B
        state["b"] = limits(95, 10)                                        # B reaches its five-hour threshold
        self.clock.now = NOW + 240
        self.assertEqual(policy.AccountPin(self.selector(reader)).target().account, "A")   # new job moves to A
        self.assertEqual(running.target().account, "B")                    # the running job still does not move

    def test_refusal_is_kept_for_the_job(self):
        calls = []

        def refuse():
            calls.append(1)
            raise policy.AccountUnavailable("quota_unavailable", "none", accounts=[], recheck_after=NOW)

        pin = policy.AccountPin(refuse)
        for _ in range(3):
            with self.assertRaises(policy.AccountUnavailable):
                pin.target()
        self.assertEqual(len(calls), 1)
        self.assertTrue(pin.refused_before_launch)


class ConfigAndCommandTest(SelectorCase):
    def test_repository_configures_exactly_a_and_b_at_twenty_percent(self):
        config = policy.load_codex_accounts()
        self.assertEqual([(a.slot, str(a.codex_home)) for a in config.accounts],
                         [("A", "/home/operator/.codex"), ("B", "/home/operator/.codex-account-b")])
        self.assertEqual((config.threshold_percent, config.recheck_seconds), (20, 60))

    def test_invalid_account_lists_are_refused(self):
        bad = [
            [{"slot": "A", "codex_home": "/x"}, {"slot": "A", "codex_home": "/y"}],
            [{"slot": "A", "codex_home": "/x"}, {"slot": "B", "codex_home": "/x"}],
            [{"slot": "a", "codex_home": "/x"}],
            [{"slot": "A", "codex_home": "relative"}],
            [{"slot": "A", "codex_home": "/mnt/c/codex"}],
            [{"slot": s, "codex_home": f"/h/{s}"} for s in "ABC"],
            [],
        ]
        for accounts in bad:
            with self.subTest(accounts=accounts):
                root = self.tmp / "cfg"
                (root / "queue").mkdir(parents=True, exist_ok=True)
                (root / "queue" / "model_routes.json").write_text(json.dumps(
                    {"routes": {"codex": {"codex_accounts": {"accounts": accounts}}}}))
                with self.assertRaises(policy.CodexPolicyError):
                    policy.load_codex_accounts(root)

    def test_every_policy_launch_forces_the_chatgpt_subscription_route(self):
        command = policy.build_exec_command()
        self.assertIn('forced_login_method="chatgpt"', command)
        self.assertIn('model_provider="openai"', command)
        self.assertLess(command.index('forced_login_method="chatgpt"'), command.index("exec"))
        with self.assertRaises(policy.CodexPolicyError):
            policy.require_subscription_environment({"OPENAI_BASE_URL": "https://proxy.invalid"})
        policy.require_subscription_environment({"PATH": "/bin"})

    def test_provider_failure_classification_reads_provider_errors_only(self):
        quota = json.dumps({"type": "turn.failed", "error": {"message": "You've hit your usage limit"}})
        self.assertEqual(policy.provider_failure_class(quota, ""), "quota_unavailable")
        self.assertEqual(policy.provider_failure_class("", "Error: 401 Unauthorized, please log in"), "needs_authentication")
        chatter = json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "fixed the 429 quota page"}})
        self.assertEqual(policy.provider_failure_class(chatter, "exit 1"), "")


class RealProbeTest(SelectorCase):
    """The real ``probe_account`` against the stand-in app-server: per-home, read-only, no billed route."""

    def test_probe_runs_in_each_homes_own_codex_home_and_never_carries_a_billed_variable(self):
        self.configure("A", limits=limits(90, 10))
        self.configure("B", limits=limits(10, 10))
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-not-real"}):
            target = self.select(reader=None)
        self.assertEqual(target.account, "B")
        for slot in "AB":
            launch = self.launches(slot)[-1]
            self.assertEqual(launch["codex_home"], str(self.homes[slot]))
            self.assertEqual(launch["billed"], [])
            self.assertEqual(launch["argv"][:5], ["app-server", "--config", 'forced_login_method="chatgpt"',
                                                  "--config", 'model_provider="openai"'])

    def test_probe_classifies_sign_in_states(self):
        for account, expected in (("error", "needs_authentication"), ("none", "needs_authentication"),
                                  ("apikey", "api_key_account")):
            with self.subTest(account=account):
                shutil.rmtree(self.state, ignore_errors=True)
                self.configure("A", account=account)
                self.configure("B", account=account)
                with self.assertRaises(policy.AccountUnavailable) as caught:
                    self.select(reader=None)
                self.assertEqual(caught.exception.failure_class, "needs_authentication")
                self.assertEqual({row["state"] for row in caught.exception.accounts}, {expected})

    def test_a_hung_probe_times_out_and_is_ineligible(self):
        self.configure("A", limits=limits(), hang=True)
        self.configure("B", limits=limits())
        reader = functools.partial(policy.probe_account, timeout=1.0)
        target = self.select(reader)
        self.assertEqual(target.account, "B")
        self.assertIn("did not answer", json.loads((self.state / "A.json").read_text())["error"]["detail"])


class StickyFiveHourSwitchTest(SelectorCase):
    """Final rule: 20% reserve on the five-hour window only; the active account is sticky."""

    def active(self) -> dict:
        return json.loads((self.state / policy.ACTIVE_ACCOUNT_FILE).read_text())

    def live_reader(self, a, b) -> tuple[FakeReader, dict]:
        state = {"A": a, "B": b}
        reader = FakeReader({slot: (lambda slot=slot: state[slot] if isinstance(state[slot], Exception)
                                    else {"account": chatgpt(), "rate_limits": state[slot]}) for slot in "AB"})
        return reader, state

    def advance(self, seconds: float = 61) -> None:
        self.clock.now += seconds

    def test_weekly_below_twenty_percent_is_still_usable(self):
        for weekly_used in (81, 85, 99):
            with self.subTest(weekly_remaining=100 - weekly_used):
                shutil.rmtree(self.state, ignore_errors=True)
                reader = self.reader(a=limits(10, weekly_used))
                target = self.select(reader)
                self.assertEqual(target.account, "A")
                self.assertEqual(reader.calls, ["A"])
                self.assertIn(f"weekly {100 - weekly_used}% remaining", target.selection.reason)
        shutil.rmtree(self.state, ignore_errors=True)
        target = self.select(self.reader(a=limits(10, 100), b=limits(10, 82)))   # weekly exhausted, B weekly 18%
        self.assertEqual(target.account, "B")
        self.assertIn("weekly exhausted at 0%", target.selection.reason)

    def test_live_like_quota_selects_and_keeps_a(self):
        # The 2026-11-17 read-only provider check: A 5h 100% / weekly 23%, B 5h 100% / weekly 18%.
        reader = self.reader(a=limits(0, 77), b=limits(0, 82))
        self.assertEqual(self.select(reader).account, "A")
        self.assertEqual(self.active()["slot"], "A")

    def test_missing_five_hour_window_fails_closed(self):
        weekly_only = {"rateLimits": {"secondary": {"usedPercent": 10, "windowDurationMins": 10080,
                                                    "resetsAt": int(NOW + 86400)}}}
        target = self.select(self.reader(a=weekly_only))
        self.assertEqual(target.account, "B")
        self.assertIn("no five-hour window", target.selection.reason)

    def test_a_to_b_to_a_round_trip_is_sticky_and_persisted(self):
        reader, state = self.live_reader(limits(10, 10), limits(10, 10))
        self.assertEqual(self.select(reader).account, "A")
        self.assertEqual((self.active()["slot"], self.active()["previous"]), ("A", None))
        self.advance()
        state["A"] = limits(80, 10)                                   # A at 20% five-hour: switch
        target = self.select(reader)
        self.assertEqual(target.account, "B")
        self.assertIn("switched A -> B", target.selection.reason)
        self.assertEqual((self.active()["slot"], self.active()["previous"]), ("B", "A"))
        for _ in range(3):                                            # A recovers; B is still eligible
            self.advance()
            state["A"] = limits(0, 10, now=self.clock.now)
            state["B"] = limits(50, 10, now=self.clock.now)
            target = self.select(reader)
            self.assertEqual(target.account, "B")
            self.assertIn("B active and eligible", target.selection.reason)
        a_reads = reader.calls.count("A")
        self.advance()
        state["B"] = limits(81, 10, now=self.clock.now)               # B at 19% five-hour: back to A
        target = self.select(reader)
        self.assertEqual(target.account, "A")
        self.assertIn("switched B -> A", target.selection.reason)
        self.assertEqual(reader.calls.count("A"), a_reads + 1)        # A re-read fresh before the switch
        self.assertEqual((self.active()["slot"], self.active()["previous"]), ("A", "B"))
        self.advance()
        state["B"] = limits(0, 10, now=self.clock.now)
        self.assertEqual(self.select(reader).account, "A")             # and A is now sticky

    def test_no_switch_when_the_other_account_does_not_qualify(self):
        reader, state = self.live_reader(limits(10, 10), limits(85, 10))
        self.assertEqual(self.select(reader).account, "A")
        self.advance()
        state["A"] = limits(90, 10)
        with self.assertRaises(policy.AccountUnavailable) as caught:
            self.select(reader)
        self.assertEqual(caught.exception.failure_class, "quota_unavailable")
        self.assertEqual(self.active()["slot"], "A")                  # a refusal never changes the active account

    def test_external_usage_is_seen_on_the_next_fresh_provider_read(self):
        # Usage from T3 / Codex CLI on the same ChatGPT account only shows up in the provider read,
        # which is cached for at most recheck_seconds.
        reader, state = self.live_reader(limits(50, 10), limits(10, 10))
        self.assertEqual(self.select(reader).account, "A")
        state["A"] = limits(85, 10)                                   # spent elsewhere, not through TTROS
        self.advance(30)
        self.assertEqual(self.select(reader).account, "A")             # bounded staleness: inside 60s
        self.advance(31)
        target = self.select(reader)
        self.assertEqual(target.account, "B")
        self.assertIn("five_hour at 15% <= 20%", target.selection.reason)

    def test_a_stale_or_foreign_snapshot_on_disk_is_never_trusted(self):
        self.state.mkdir(parents=True)
        old = {"version": 1, "slot": "A", "codex_home": str(self.homes["A"]), "basis": "x", "error": None,
               "checked_at": NOW - 3600, "snapshot": {"account_type": "chatgpt", "rate_limits": {"windows": [
                   {"source": "primary", "used_percent": 0, "window_minutes": 300, "resets_at": int(NOW + 600)}]}}}
        (self.state / "A.json").write_text(json.dumps(old))
        foreign = dict(old, checked_at=NOW, codex_home="/elsewhere")
        (self.state / "B.json").write_text(json.dumps(foreign))
        reader = self.reader(a=limits(90, 10), b=limits(10, 10))
        self.assertEqual(self.select(reader).account, "B")
        self.assertEqual(sorted(reader.calls), ["A", "B"])

    def test_active_provider_failure_moves_on_and_double_failure_keeps_active(self):
        reader, state = self.live_reader(limits(90, 10), limits(10, 10))
        self.assertEqual(self.select(reader).account, "B")
        self.advance()
        state["A"] = limits(10, 10)
        state["B"] = RuntimeError("app-server did not answer")         # active B cannot be read
        target = self.select(reader)
        self.assertEqual(target.account, "A")
        self.assertIn("B not eligible (quota_unknown", target.selection.reason)
        self.advance()
        state["A"] = RuntimeError("401 Unauthorized: refresh token expired")
        state["B"] = RuntimeError("app-server did not answer")
        with self.assertRaises(policy.AccountUnavailable) as caught:
            self.select(reader)
        self.assertEqual({row["slot"]: row["state"] for row in caught.exception.accounts},
                         {"A": "needs_authentication", "B": "quota_unknown"})
        self.assertEqual(self.active()["slot"], "A")

    def test_corrupt_or_unconfigured_active_record_falls_back_to_the_first_account(self):
        for content in ("{not json", json.dumps({"version": 1, "slot": "C"}), json.dumps({"version": 9, "slot": "B"})):
            with self.subTest(content=content):
                shutil.rmtree(self.state, ignore_errors=True)
                self.state.mkdir(parents=True)
                (self.state / policy.ACTIVE_ACCOUNT_FILE).write_text(content)
                reader = self.reader()
                self.assertEqual(self.select(reader).account, "A")
                self.assertEqual(reader.calls, ["A"])
                self.assertEqual(self.active()["slot"], "A")

    def test_concurrent_selections_during_a_switch_agree_and_persist_once(self):
        reader, state = self.live_reader(limits(10, 10), limits(10, 10))
        self.assertEqual(self.select(reader).account, "A")
        self.advance()
        state["A"] = limits(95, 10)
        reader.delay = 0.1
        results, errors = [], []
        writes = []
        real_write = policy._write_snapshot

        def counting_write(path, entry):
            if path.name == policy.ACTIVE_ACCOUNT_FILE:
                writes.append(entry["slot"])
            real_write(path, entry)

        def launch():
            try:
                results.append(self.select(reader).account)
            except Exception as exc:  # pragma: no cover - reported below
                errors.append(exc)

        with patch.object(policy, "_write_snapshot", counting_write):
            threads = [threading.Thread(target=launch) for _ in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(10)
        self.assertEqual(errors, [])
        self.assertEqual(results, ["B"] * 8)
        self.assertEqual(writes, ["B"])                               # one switch, not eight
        self.assertEqual(self.active()["previous"], "A")


CHILD_SELECT = textwrap.dedent("""\
    import json, sys
    from dataclasses import replace
    from pathlib import Path
    sys.path.insert(0, sys.argv[1])
    import aos_codex_policy as policy
    homes, state, cli = json.loads(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4])
    config = policy.AccountConfig(tuple(policy.CodexAccount(s, Path(h)) for s, h in homes.items()), 20, 60)
    base = replace(policy.CODEX_TARGET, executable=cli, linux_user=policy.effective_linux_user())
    try:
        print(policy.select_codex_account(config=config, state_dir=state, base=base).account)
    except policy.AccountUnavailable as exc:
        print("refused:" + exc.failure_class)
""")


class StickyAcrossProcessesTest(SelectorCase):
    """Separate OS processes (a restart, parallel runners) share the persisted choice."""

    def child(self) -> subprocess.Popen:
        script = self.tmp / "child_select.py"
        if not script.exists():
            script.write_text(CHILD_SELECT, encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k not in BILLED}
        return subprocess.Popen([sys.executable, "-I", str(script), str(ROOT / "tools"),
                                 json.dumps({s: str(h) for s, h in self.homes.items()}), str(self.state), str(self.cli)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)

    def run_child(self) -> str:
        proc = self.child()
        out, err = proc.communicate(timeout=60)
        self.assertEqual(proc.returncode, 0, err)
        return out.strip()

    def expire_snapshots(self) -> None:
        for slot in "AB":
            path = self.state / f"{slot}.json"
            if path.exists():
                entry = json.loads(path.read_text())
                entry["checked_at"] = time.time() - 3600
                path.write_text(json.dumps(entry))

    def test_restart_resumes_the_active_account_and_switches_back_on_threshold(self):
        now = time.time()
        self.configure("A", limits=limits(85, 10, now=now))
        self.configure("B", limits=limits(10, 10, now=now))
        self.assertEqual(self.run_child(), "B")
        self.expire_snapshots()
        self.configure("A", limits=limits(0, 10, now=now))            # A recovered while "restarting"
        self.assertEqual(self.run_child(), "B")                       # a new process stays on B
        self.expire_snapshots()
        self.configure("B", limits=limits(80, 10, now=now))           # B reaches 20% five-hour
        self.assertEqual(self.run_child(), "A")
        saved = json.loads((self.state / policy.ACTIVE_ACCOUNT_FILE).read_text())
        self.assertEqual((saved["slot"], saved["previous"]), ("A", "B"))

    def test_parallel_processes_agree_and_probe_each_home_once(self):
        now = time.time()
        self.configure("A", limits=limits(90, 10, now=now))
        self.configure("B", limits=limits(10, 10, now=now))
        procs = [self.child() for _ in range(6)]
        outputs = []
        for proc in procs:
            out, err = proc.communicate(timeout=60)
            self.assertEqual(proc.returncode, 0, err)
            outputs.append(out.strip())
        self.assertEqual(outputs, ["B"] * 6)
        probes = {slot: [r for r in self.launches(slot) if r["argv"][:1] == ["app-server"]] for slot in "AB"}
        self.assertEqual((len(probes["A"]), len(probes["B"])), (1, 1))


# -- launchers ------------------------------------------------------------------------

class ModelPortLaunchTest(SelectorCase):
    def setUp(self):
        super().setUp()
        self.ledger_root = self.tmp / "ledger"
        (self.ledger_root / "queue").mkdir(parents=True)
        self.configure("A", limits=limits(85, 10))
        self.configure("B", limits=limits(10, 10))

    def call(self, **kwargs):
        options = {"model": "gpt-6-luna", "effort": "low", "output_schema": SCHEMA, "root": self.ledger_root,
                   "agent": "fixture.agent", "work_item_id": "AOS-2026-10001", "timeout": 20}
        options.update(kwargs)
        return modelport.call("Classify this note.", **options)

    def ledger(self) -> list[dict]:
        path = self.ledger_root / "queue" / "token_ledger.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        return [row for row in rows if row.get("event") == "model_invocation"]

    def test_unpinned_call_selects_and_launches_in_the_selected_home(self):
        with patch.object(modelport, "select_codex_account", self.selector()):
            result = self.call()
        self.assertEqual(result["status"], "ok", result["detail"])
        self.assertEqual(result["codex_account"]["slot"], "B")
        exec_launch = [row for row in self.launches("B") if "exec" in row["argv"]]
        self.assertEqual(len(exec_launch), 1)
        self.assertEqual(exec_launch[0]["codex_home"], str(self.homes["B"]))
        self.assertFalse([row for row in self.launches("A") if "exec" in row["argv"]])  # A only probed
        rows = self.ledger()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["invocation_id"], result["invocation_id"])
        self.assertEqual(rows[0]["model_selection"]["codex_account"]["slot"], "B")
        self.assertEqual(rows[0]["exact_usage"]["canonical_total"], 1250)

    def test_job_pin_keeps_every_call_on_one_account(self):
        pin = policy.AccountPin(self.selector())
        first = self.call(account=pin)
        self.configure("A", limits=limits(0, 0))                     # A recovers mid-job
        self.clock.now = NOW + 120
        second = self.call(account=pin)
        self.assertEqual((first["codex_account"]["slot"], second["codex_account"]["slot"]), ("B", "B"))
        self.assertEqual([row["model_selection"]["codex_account"]["slot"] for row in self.ledger()], ["B", "B"])
        self.assertEqual(len({row["invocation_id"] for row in self.ledger()}), 2)   # one row per invocation

    def test_no_eligible_account_refuses_without_launch_or_ledger_row(self):
        self.configure("B", account="none")
        result = self.call(account=policy.AccountPin(self.selector()))
        self.assertEqual((result["status"], result["failure_class"]), ("refused", "quota_unavailable"))
        self.assertEqual(result["usage"], {"available": False})
        self.assertFalse([row for slot in "AB" for row in self.launches(slot) if "exec" in row["argv"]])
        self.assertEqual(self.ledger(), [])
        self.assertEqual({row["slot"]: row["state"] for row in result["codex_account"]["accounts"]},
                         {"A": "below_threshold", "B": "needs_authentication"})

    def test_billed_route_variable_refuses_before_any_account_read(self):
        with patch.dict(os.environ, {"CODEX_API_KEY": "not-real"}), \
                patch.object(modelport, "select_codex_account", side_effect=AssertionError("no read")):
            result = self.call()
        self.assertEqual((result["status"], result["failure_class"]), ("refused", "preflight_refused"))
        self.assertIn("CODEX_API_KEY", result["detail"])

    def test_explicit_env_still_receives_the_selected_codex_home(self):
        target = policy.account_target(policy.CodexAccount("B", self.homes["B"]), self.base)
        result = self.call(target=target, env={"PATH": os.environ.get("PATH", ""), "CODEX_HOME": str(self.homes["A"])})
        self.assertEqual(result["status"], "ok", result["detail"])
        self.assertEqual(self.launches("B")[-1]["codex_home"], str(self.homes["B"]))

    def test_warm_session_of_another_account_is_never_mixed(self):
        a_target = policy.account_target(policy.CodexAccount("A", self.homes["A"]), self.base)
        b_target = policy.account_target(policy.CodexAccount("B", self.homes["B"]), self.base)
        with modelport.AppServerSession(target=a_target) as session:
            result = self.call(backend="app-server", session=session, target=b_target)
        self.assertEqual((result["status"], result["failure_class"]), ("refused", "preflight_refused"))
        self.assertIn("credentials are never mixed", result["detail"])

    def test_real_provider_quota_error_is_quota_unavailable(self):
        self.configure("B", limits=limits(10, 10), mode="quota")
        result = self.call(account=policy.AccountPin(self.selector()))
        self.assertEqual((result["status"], result["failure_class"]), ("failed", "quota_unavailable"))
        self.assertEqual(self.ledger()[0]["model_selection"]["codex_account"]["slot"], "B")   # recorded as unknown, B


class DavidTurnAccountTest(SelectorCase):
    def setUp(self):
        super().setUp()
        self.configure("A", limits=limits(10, 10))
        self.configure("B", limits=limits(10, 10))
        self.root = self.tmp / "root"
        (self.root / "queue").mkdir(parents=True)
        self.david = aos_david.David(self.root, warm=True, account_selector=self.selector(),
                                     search=lambda _q: [], threads=object())
        self.addCleanup(self.david.close)

    def call(self, account=None):
        return self.david._call("hello", aos_david.FAST_MODEL, account)

    def test_turns_use_the_selected_accounts_warm_server_and_inflight_turns_keep_theirs(self):
        first = self.david._turn_account()
        self.assertEqual(self.call(first)["codex_account"]["slot"], "A")
        session_a = self.david._session
        self.configure("A", limits=limits(95, 10))                       # A falls below the reserve
        self.clock.now = NOW + 61
        self.assertEqual(self.call(first)["codex_account"]["slot"], "A")  # same turn: same account
        self.assertEqual(self.call()["codex_account"]["slot"], "B")       # a new turn moves to B
        self.assertIsNotNone(session_a.process)                          # A's server was not torn down
        self.assertIsNot(self.david._session, session_a)
        app_servers = {slot: [r for r in self.launches(slot) if r["argv"][:1] == ["app-server"]] for slot in "AB"}
        self.assertTrue(all(r["codex_home"] == str(self.homes[slot]) for slot in "AB" for r in app_servers[slot]))
        self.configure("A", limits=limits(0, 10, now=NOW + 200))
        self.clock.now = NOW + 200
        self.assertEqual(self.call()["codex_account"]["slot"], "B")       # A eligible again: B stays active
        self.configure("B", limits=limits(90, 10, now=NOW + 300))        # B reaches its five-hour threshold
        self.clock.now = NOW + 300
        self.assertEqual(self.call()["codex_account"]["slot"], "A")       # new turns move back to A
        self.assertIs(self.david._session, session_a)                    # and reuse A's warm server

    def test_no_account_is_a_truthful_refusal_and_deterministic_routes_still_answer(self):
        self.configure("A", account="none")
        self.configure("B", account="error")
        result = self.call()
        self.assertEqual((result["status"], result["failure_class"]), ("refused", "needs_authentication"))
        failure = aos_david._model_failure(result)
        self.assertEqual(failure[0], "needs_authentication")
        self.assertIn("no Codex account is signed in and eligible", failure[1])
        reply = self.david.turn("conv-1", "/jobs")
        self.assertEqual(reply["model_calls"], 0)


class JobAccountTest(SelectorCase):
    """A real job through aos_jobs -> executor -> runtime -> ModelPort -> stand-in exec."""

    HANDLER = ("def run(input, ctx):\n"
               "    first = ctx.model('one')\n"
               "    second = ctx.model('two')\n"
               "    return {'status': 'ok', 'output': {'summary': first['summary'], 'second': second['summary']}}\n")

    def setUp(self):
        super().setUp()
        from tests.test_aos_agents_runtime import make_root, write_package

        self.root = make_root()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        write_package(self.root, "fixture.twocall", self.HANDLER, kind="oneshot")
        registry = json.loads((self.root / "agents" / "registry.lock.json").read_text())
        registry.setdefault("agents", {})["fixture.twocall"] = {"state": "canary"}
        (self.root / "agents" / "registry.lock.json").write_text(json.dumps(registry))
        released = patch.dict(os.environ, {aos_jobs.RELEASE_STATES_ENV: "canary,active"})
        released.start()
        self.addCleanup(released.stop)
        self.configure("A", limits=limits(85, 10))
        self.configure("B", limits=limits(10, 10))

    def job(self):
        job, _ = aos_jobs.create(self.root, conversation_id="conv-accounts", topic="accounts", request="two calls",
                                 executor={"kind": "agent", "agent": "fixture.twocall@0.1.0", "input": {}})
        return job["id"]

    def ledger(self) -> list[dict]:
        path = self.root / "queue" / "token_ledger.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        return [row for row in rows if row.get("event") == "model_invocation"]

    def test_job_runs_on_one_selected_account_and_records_it(self):
        job_id = self.job()
        result = aos_jobs.execute(self.root, job_id, codex_account=policy.AccountPin(self.selector()))
        self.assertEqual(result["status"], "done", result.get("detail"))
        self.assertEqual(result["codex_account"]["slot"], "B")
        execs = [row for row in self.launches("B") if "exec" in row["argv"]]
        self.assertEqual(len(execs), 2)
        self.assertFalse([row for row in self.launches("A") if "exec" in row["argv"]])
        rows = [row for row in self.ledger() if row.get("parent_work_item_id") == job_id or row.get("item_id") == job_id]
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["model_selection"]["codex_account"]["slot"] for row in rows}, {"B"})
        receipt = (self.root / result["receipts"][0]["path"]).read_text()
        self.assertIn("Codex account: B", receipt)
        self.assertIn("Token usage: 2500 known", receipt)

    def test_no_account_defers_the_job_without_failing_it_then_runs_once_capacity_returns(self):
        self.configure("B", account="error")
        job_id = self.job()
        deferred = aos_jobs.execute(self.root, job_id, codex_account=policy.AccountPin(self.selector()))
        self.assertEqual(deferred["status"], "queued")
        self.assertEqual(deferred["attempts"], 0)                        # the attempt is not consumed
        self.assertEqual(deferred["deferred"]["count"], 1)
        self.assertEqual(deferred["deferred"]["failure_class"], "quota_unavailable")
        self.assertIn("waiting for Codex capacity", deferred["detail"])
        self.assertFalse(deferred.get("receipts"))                       # not a settle point: no receipt, no notice
        self.assertEqual(self.ledger(), [])
        self.assertFalse([row for slot in "AB" for row in self.launches(slot) if "exec" in row["argv"]])
        runner = aos_jobs.JobRunner(self.root, workers=1)
        self.addCleanup(runner.shutdown)
        delay = runner.recheck_delay(aos_jobs.load(self.root, job_id))
        self.assertTrue(aos_jobs.DEFERRAL_MIN_SECONDS <= delay <= aos_jobs.DEFERRAL_MAX_SECONDS)
        self.configure("B", limits=limits(10, 10))                       # Casey signed B in
        self.clock.now = NOW + 120
        done = aos_jobs.execute(self.root, job_id, codex_account=policy.AccountPin(self.selector()))
        self.assertEqual(done["status"], "done", done.get("detail"))
        self.assertEqual((done["attempts"], done["codex_account"]["slot"]), (1, "B"))
        self.assertEqual(len(self.ledger()), 2)                          # each call recorded exactly once

    def test_deferral_rechecks_are_bounded(self):
        runner = aos_jobs.JobRunner(self.root, workers=1)
        self.addCleanup(runner.shutdown)
        job = {"status": "queued", "deferred": {"count": aos_jobs.MAX_DEFERRALS + 1, "recheck_after": "2000-01-01T00:00:00Z"}}
        self.assertIsNone(runner.recheck_delay(job))
        job["deferred"]["count"] = 1
        self.assertEqual(runner.recheck_delay(job), aos_jobs.DEFERRAL_MIN_SECONDS)   # past due -> floor
        job["deferred"]["recheck_after"] = "2999-01-01T00:00:00Z"
        self.assertEqual(runner.recheck_delay(job), aos_jobs.DEFERRAL_MAX_SECONDS)   # far reset -> cap
        self.assertIsNone(runner.recheck_delay({"status": "done", "deferred": {"count": 1}}))

    def test_runner_resubmits_a_deferred_job_once_its_delay_passes(self):
        runs = []

        def fake_execute(root, job_id, **_kwargs):
            runs.append(job_id)
            if len(runs) == 1:
                return {"status": "queued", "deferred": {"count": 1, "recheck_after": "2000-01-01T00:00:00Z"}}
            return {"status": "done"}

        with patch.object(aos_jobs, "execute", fake_execute), patch.object(aos_jobs, "DEFERRAL_MIN_SECONDS", 0.05):
            runner = aos_jobs.JobRunner(self.root, workers=1)
            runner.submit("AOS-2026-10001").result(5)
            deadline = time.time() + 5
            while len(runs) < 2 and time.time() < deadline:
                time.sleep(0.02)
            runner.shutdown(wait=True)
        self.assertEqual(runs, ["AOS-2026-10001", "AOS-2026-10001"])
        self.assertEqual(runner._timers, {})

    def test_runtime_passes_the_job_pin_to_every_model_call(self):
        seen = []

        def port(prompt, **kwargs):
            seen.append(kwargs.get("account"))
            return {"status": "ok", "ok": True, "json": {"summary": "s"}, "usage": {"available": False},
                    "invocation_id": f"x{len(seen)}", "codex_account": {"slot": "B"}}

        pin = policy.AccountPin(self.selector())
        result = run_agent("fixture.twocall@0.1.0", {}, RunEnv(root=self.root, modelport=port, record=False, codex_account=pin))
        self.assertEqual(result["status"], "ok", result.get("detail"))
        self.assertEqual(seen, [pin, pin])
        self.assertEqual([call["codex_account"] for call in result["usage"]["calls"]], ["B", "B"])

    # -- selector x M7 integration ------------------------------------------------
    def attempts(self, job_id) -> list[dict]:
        from aos_agents.journal import StepJournal

        path = StepJournal(self.root, job_id).path.with_suffix(".model-attempts.jsonl")
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []

    def test_deferred_job_journals_the_refused_attempt_as_never_launched(self):
        from aos_agents import reliability

        self.configure("B", account="error")
        job_id = self.job()
        deferred = aos_jobs.execute(self.root, job_id, codex_account=policy.AccountPin(self.selector()))
        self.assertEqual(deferred["status"], "queued")
        self.assertEqual([row["result"]["status"] for row in self.attempts(job_id)], ["started", "not_launched"])
        self.assertEqual(reliability.launched_model_calls(self.attempts(job_id)), [])
        self.configure("B", limits=limits(10, 10))
        self.clock.now = NOW + 120
        done = aos_jobs.execute(self.root, job_id, codex_account=policy.AccountPin(self.selector()))
        self.assertEqual(done["status"], "done", done.get("detail"))
        rows = self.attempts(job_id)
        self.assertEqual([row["result"]["status"] for row in rows], ["started", "not_launched", "started", "started"])
        launched = reliability.launched_model_calls(rows)
        # Two real launches, matching the two ledger rows; the refused first try is not a repeated call.
        self.assertEqual(len(launched), 2)
        self.assertEqual(len(set(launched)), 2)
        self.assertEqual(len(self.ledger()), 2)

    def test_no_signed_in_account_mid_job_is_a_truthful_block(self):
        # An injected port has no pin, so aos_jobs cannot defer: the runtime must still end the
        # job as blocked (never failed, retried or rerouted) on the selector's refusal class.
        calls = []

        def port(prompt, **kwargs):
            calls.append(prompt)
            return {"status": "refused", "failure_class": "needs_authentication", "detail": "no account signed in",
                    "usage": {"available": False}, "invocation_id": "refused-1", "codex_account": None}

        job_id = self.job()
        done = aos_jobs.execute(self.root, job_id, modelport=port)
        self.assertEqual((done["status"], done["failure_class"]), ("blocked", "needs_authentication"))
        self.assertEqual(len(calls), 1)
        self.assertEqual([row["result"]["status"] for row in self.attempts(job_id)], ["started", "not_launched"])
        self.assertIn("Status: blocked", (self.root / done["receipts"][0]["path"]).read_text())

    # -- Gate 7 forced failure: a controlled post-launch stop, through the real path -----
    def forced_probe(self, mode: str, failure_class: str):
        """Call 1 succeeds, call 2 is launched and then stopped by the (stand-in) provider."""
        from aos_agents import reliability
        from aos_agents.package import load_package

        self.configure("B", limits=limits(10, 10), mode=mode, fail_from=2)
        job_id = self.job()
        done = aos_jobs.execute(self.root, job_id, codex_account=policy.AccountPin(self.selector()))
        self.assertEqual((done["status"], done["failure_class"]), ("blocked", failure_class), done.get("detail"))
        # Durable receipt; the job is terminal and is never re-run, retried or moved to account A.
        receipt = (self.root / done["receipts"][0]["path"]).read_text()
        self.assertIn("Status: blocked", receipt)
        self.assertIn("Codex account: B", receipt)
        execs = [row for row in self.launches("B") if "exec" in row["argv"]]
        self.assertEqual(len(execs), 2)
        self.assertEqual(aos_jobs.execute(self.root, job_id, codex_account=policy.AccountPin(self.selector()))["status"], "blocked")
        self.assertEqual(len([row for row in self.launches("B") if "exec" in row["argv"]]), 2)
        self.assertFalse([row for row in self.launches("A") if "exec" in row["argv"]])
        self.assertFalse([row for slot in "AB" for row in self.launches(slot) if row["billed"]])
        # Both calls were launched (no not_launched close) and each is in the ledger exactly once.
        rows = [row["result"]["status"] for row in self.attempts(job_id)]
        self.assertEqual(rows, ["started", "started"])
        self.assertEqual(len(set(reliability.launched_model_calls(self.attempts(job_id)))), 2)
        ledger = [row for row in self.ledger() if row.get("item_id") == job_id]
        self.assertEqual(len(ledger), 2)
        self.assertEqual({row["model_selection"]["transport"]["production_binary"] for row in ledger}, {False})
        self.assertFalse(done.get("effects"))
        captured = reliability.capture_forced_failure(self.root, job_id, "quota" if "quota" in mode else "auth",
                                                      evidence_class="injected_transport", simulated=True)
        facts = reliability.forced_failure_summary(captured, load_package("fixture.twocall", self.root))
        self.assertEqual(facts, {"kind": "quota" if "quota" in mode else "auth", "evidence_class": "injected_transport",
                                 "post_launch": True, "outcome": "blocked", "truthful": True, "billed_fallback": False})
        # The same run can never be passed off as a genuine provider failure.
        with self.assertRaisesRegex(ValueError, "contradicts its transport provenance"):
            reliability.forced_failure_summary({**captured, "evidence_class": "genuine_provider"},
                                               load_package("fixture.twocall", self.root))
        return captured

    def test_post_launch_quota_stop_is_a_truthful_block_with_injected_evidence(self):
        self.forced_probe("post_launch_quota", "quota_unavailable")

    def test_post_launch_auth_stop_is_a_truthful_block_with_injected_evidence(self):
        self.forced_probe("post_launch_auth", "authentication_unavailable")

    def test_pre_launch_refusal_is_not_a_post_launch_probe(self):
        from aos_agents import reliability

        self.configure("B", account="error")
        job_id = self.job()
        deferred = aos_jobs.execute(self.root, job_id, codex_account=policy.AccountPin(self.selector()))
        self.assertEqual(deferred["status"], "queued")                       # requeued: no receipt to capture
        self.assertEqual(reliability.launched_model_calls(self.attempts(job_id)), [])
        with self.assertRaisesRegex(ValueError, "never settled"):
            reliability.capture_forced_failure(self.root, job_id, "auth", evidence_class="injected_transport")

    def test_direct_agent_output_is_journaled_for_gate7_collection(self):
        from aos_agents import reliability
        from aos_agents.journal import StepJournal
        from aos_agents.package import load_package

        self.configure("B", limits=limits(10, 10))
        job_id = self.job()
        done = aos_jobs.execute(self.root, job_id, codex_account=policy.AccountPin(self.selector()))
        self.assertEqual(done["status"], "done", done.get("detail"))
        identity = done["result"]["steps"][0]["execution_identity"]
        output = reliability.journaled_output(self.root, job_id, load_package("fixture.twocall", self.root), identity["output_sha256"])
        self.assertEqual(output, {"summary": "ok", "second": "ok"})
        record = StepJournal(self.root, job_id).records()[-1]
        self.assertEqual((record["plan"], record["step"], record["input_sha256"]),
                         ("agent:fixture.twocall@0.1.0", "run", identity["input_sha256"]))
        with self.assertRaisesRegex(ValueError, "not in the step journal"):
            reliability.journaled_output(self.root, job_id, load_package("fixture.twocall", self.root), "0" * 64)

    def test_transport_flag_compares_the_launched_binary_with_the_production_one(self):
        self.configure("B", limits=limits(10, 10))
        job_id = self.job()
        aos_jobs.execute(self.root, job_id, codex_account=policy.AccountPin(self.selector()))
        transports = [row["model_selection"]["transport"] for row in self.ledger() if row.get("item_id") == job_id]
        self.assertEqual(transports, [{"executable": str(self.cli), "production_binary": False}] * 2)
        # Were this binary the production one, the same runtime path would record it as such.
        production = replace(policy.CODEX_TARGET, executable=self.cli)
        with patch.object(modelport, "CODEX_TARGET", production):
            second = self.job()
            aos_jobs.execute(self.root, second, codex_account=policy.AccountPin(self.selector()))
        flags = {row["model_selection"]["transport"]["production_binary"] for row in self.ledger() if row.get("item_id") == second}
        self.assertEqual(flags, {True})


class FactoryAccountTest(SelectorCase):
    def test_factory_quota_gate_reads_the_pinned_accounts_windows(self):
        self.configure("A", limits=limits(85, 10))
        self.configure("B", limits=limits(30, 40))
        pin = policy.AccountPin(self.selector())
        snapshot = factory._read_rate_limits(pin)
        self.assertEqual(pin.granted.account, "B")
        self.assertEqual(snapshot["rateLimits"]["primary"]["usedPercent"], 30)
        gate = factory.quota_preflight(reader=lambda: factory._read_rate_limits(pin))
        self.assertTrue(gate["ok"], gate)

    def test_factory_with_no_account_stops_at_the_quota_step(self):
        self.configure("A", account="none")
        self.configure("B", account="none")
        pin = policy.AccountPin(self.selector())
        gate = factory.quota_preflight(reader=lambda: factory._read_rate_limits(pin))
        self.assertFalse(gate["ok"])
        self.assertIn("needs_authentication", gate["detail"])
        self.assertTrue(pin.refused_before_launch)


class SourceInterpretAccountTest(SelectorCase):
    def test_interpretation_launches_on_the_selected_home_and_records_the_slot(self):
        from tools import aos_codex_policy as tools_policy
        from tools import source_interpret as si

        self.configure("A", limits=limits(85, 10))
        self.configure("B", limits=limits(10, 10), answer={"topics": []})
        base = replace(tools_policy.CODEX_TARGET, executable=self.cli, linux_user=tools_policy.effective_linux_user())
        chosen = tools_policy.account_target(tools_policy.CodexAccount("B", self.homes["B"]), base,
                                             tools_policy.AccountSelection("B", "fixture", "fixture", "2026-11-16T00:00:00Z"))
        ledger_root = self.tmp / "ledger"
        (ledger_root / "queue").mkdir(parents=True)
        with patch.object(tools_policy, "readiness", return_value={"available": True, "authenticated": True}):
            call = si.call_luna("Index this.", root=ledger_root, sha="a" * 64, target=chosen)
        self.assertEqual(call["codex_account"]["slot"], "B")
        launch = [row for row in self.launches("B") if "exec" in row["argv"]][-1]
        self.assertIn('forced_login_method="chatgpt"', launch["argv"])
        self.assertEqual(launch["codex_home"], str(self.homes["B"]))
        row = si.record_usage(call, root=ledger_root, sha="a" * 64)
        self.assertTrue(row.get("recorded"), row)
        saved = [json.loads(line) for line in (ledger_root / "queue" / "token_ledger.jsonl").read_text().splitlines()]
        self.assertEqual(saved[-1]["model_selection"]["codex_account"]["slot"], "B")

    def test_no_account_raises_before_a_paid_attempt_is_counted(self):
        from tools import aos_codex_policy as tools_policy
        from tools import source_interpret as si

        refusal = tools_policy.AccountUnavailable("quota_unavailable", "A and B low", accounts=[], recheck_after=NOW)
        with patch.object(tools_policy, "select_codex_account", side_effect=refusal):
            with self.assertRaises(si.RateLimited):
                si.select_luna_account()
        auth = tools_policy.AccountUnavailable("needs_authentication", "sign in", accounts=[], recheck_after=NOW)
        with patch.object(tools_policy, "select_codex_account", side_effect=auth):
            with self.assertRaises(si.ModelUnavailable):
                si.select_luna_account()

    def test_billed_route_variable_refuses_interpretation(self):
        from tools import aos_codex_policy as tools_policy
        from tools import source_interpret as si

        chosen = tools_policy.account_target(tools_policy.CodexAccount("B", self.homes["B"]),
                                             replace(tools_policy.CODEX_TARGET, executable=self.cli))
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-not-real"}):
            with self.assertRaises(si.ModelUnavailable):
                si.call_luna("Index this.", root=self.tmp, sha="b" * 64, target=chosen)
        self.assertEqual(self.launches("B"), [])


if __name__ == "__main__":
    unittest.main()
