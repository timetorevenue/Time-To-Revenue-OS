"""Deterministic gate for untrusted contributions (pull requests) to TTROS Core.

    python3 tools/contribution_gate.py check --base <ref> --head <ref> [--json]

Reads only git objects (`git diff`); never executes contributor code, never reads credentials, makes no
network or model call. Exit 0 = nothing blocked, 1 = blocked, 2 = usage error. Maintainer review is still
required for every merge; this gate removes the cheap failures and flags the risky paths.

BLOCKED  secrets, private-looking identifiers, credential/database/key files, binaries, any change to release
         state (agent registry, canary acceptances), CI configuration or this gate itself. A maintainer makes
         those changes in a separate reviewed commit, never in a contribution.
REVIEW   code that decides permissions, credentials, connections, network exposure or process execution.
         Allowed, but flagged: such changes must be reviewed in an isolated environment, never on a machine
         that holds real credentials or a real Business Brain.
Contributed agent packages enter the Forge only as `candidate`; promotion needs the maintainer's evaluation gates.

Revisit: when a new protected path or secret format appears. · Last touched: 2026-11-26.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import re
import subprocess
import sys

BLOCKED_PATHS = [
    ".github/**", "tools/contribution_gate.py", "tests/test_contribution_gate.py", "SECURITY.md", "CODEOWNERS",
    "agents/registry.lock.json", "agents/canary_acceptances.json", "tests/fixtures/core_context/registry.lock.json",
    "**/.env", "**/.env.*", "**/*.pem", "**/*.key", "**/id_rsa*", "**/id_ed25519*", "**/credentials*.json",
    "**/*.sqlite", "**/*.sqlite3", "**/*.db", "queue/jobs/**", "queue/receipts/**", "queue/crm/**",
]
REVIEW_PATHS = [
    "rules/**", "connectors/**", "systemd/**", "scripts/**", "conftest.py", "core_skips_plugin.py", "requirements.txt",
    "dashboard/frontend/package.json", "dashboard/frontend/package-lock.json", "dashboard/backend/requirements.txt",
    "dashboard/backend/main.py", "dashboard/backend/dashboard_identity.py", "dashboard/backend/account_connections.py",
    "tools/aos_authority*.py", "tools/aos_paths.py", "tools/aos_agents/**", "tools/business_brain_scope.py",
    "context/client_scope_registry.json", "context/account_connections.json", "agents/packages/**",
]
SECRETS = [
    ("aws-key", r"\bAKIA[0-9A-Z]{16}\b"), ("private-key", r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"),
    ("github-token", r"\b(?:ghp|gho|ghu|ghs|ghr|github_pat)_[A-Za-z0-9_]{20,}"), ("slack-token", r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    ("api-key", r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{24,}"), ("google-key", r"\bAIza[0-9A-Za-z_-]{35}\b"),
    ("stripe-live", r"\b(?:sk|rk|pk)_live_[0-9A-Za-z]{16,}"), ("jwt", r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    ("telegram-token", r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b"),
    ("assigned-secret", r"(?i)\b(?:api[_-]?key|secret|token|passw(?:or)?d)\b['\"]?\s*[:=]\s*['\"][A-Za-z0-9/+_=.-]{24,}['\"]"),
]
PRIVATE = [
    ("host-path", r"/home/(?!operator\b|user\b|runner\b)[a-z][\w.-]*/|/mnt/[cg]/Users/(?!Operator\b)|C:\\{1,2}Users\\{1,2}(?!Operator\b)"),
    ("real-email", r"[A-Za-z0-9._%+-]+@(?!(?:[A-Za-z0-9-]+\.)*(?:example|invalid|test|localhost)\b|users\.noreply\.github\.com|anthropic\.com)[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    ("private-ip", r"\b(?:10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d+\.\d+)\b"),
]
SECRET_RX = [(n, re.compile(p)) for n, p in SECRETS]
PRIVATE_RX = [(n, re.compile(p)) for n, p in PRIVATE]


def glob_re(pat: str) -> re.Pattern:
    out, i = "", 0
    while i < len(pat):
        if pat.startswith("**/", i): out += "(?:.*/)?"; i += 3; continue
        if pat.startswith("**", i): out += ".*"; i += 2; continue
        c = pat[i]
        out += "[^/]*" if c == "*" else re.escape(c)
        i += 1
    return re.compile("^" + out + "$")


BLOCKED_RX = [glob_re(p) for p in BLOCKED_PATHS]
REVIEW_RX = [glob_re(p) for p in REVIEW_PATHS]


def git(*args: str, repo: str | None = None) -> str:
    cmd = ["git", *(["-C", repo] if repo else []), *args]
    done = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    if done.returncode:
        raise SystemExit(f"git failed: {' '.join(args)}\n{done.stderr.strip()}")
    return done.stdout


def check(base: str, head: str, repo: str | None = None) -> dict:
    status = git("diff", "--name-status", "-M", "--no-renames", f"{base}...{head}", repo=repo).splitlines()
    numstat = {l.split("\t")[2]: l.split("\t")[0] for l in git("diff", "--numstat", "--no-renames", f"{base}...{head}", repo=repo).splitlines() if l.count("\t") >= 2}
    blocked, review = [], []
    for line in status:
        code, _, path = line.partition("\t")
        if any(r.match(path) for r in BLOCKED_RX):
            blocked.append({"path": path, "reason": "protected-path"})
        elif any(r.match(path) for r in REVIEW_RX):
            review.append({"path": path, "reason": "permission-sensitive"})
        if numstat.get(path) == "-" and code != "D":
            blocked.append({"path": path, "reason": "binary-file"})
    patch = git("diff", "-U0", "--no-renames", f"{base}...{head}", repo=repo)
    current = ""
    for raw in patch.splitlines():
        if raw.startswith("+++ b/"): current = raw[6:]; continue
        if not raw.startswith("+") or raw.startswith("+++"): continue
        text = raw[1:]
        for name, rx in (*SECRET_RX, *PRIVATE_RX):
            if rx.search(text):
                blocked.append({"path": current, "reason": name})
    uniq = lambda rows: sorted({(r["path"], r["reason"]) for r in rows})
    return {"blocked": [dict(zip(("path", "reason"), r)) for r in uniq(blocked)],
            "review": [dict(zip(("path", "reason"), r)) for r in uniq(review)],
            "files": len(status), "ok": not blocked}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--base", required=True); c.add_argument("--head", required=True)
    c.add_argument("--repo"); c.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result = check(args.base, args.head, args.repo)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        for row in result["blocked"]: print(f"BLOCKED  {row['path']}: {row['reason']}")
        for row in result["review"]: print(f"REVIEW   {row['path']}: {row['reason']}")
        print(f"{result['files']} file(s) changed; {'OK' if result['ok'] else 'blocked'}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
