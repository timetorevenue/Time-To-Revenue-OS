"""Websites/Digital: deterministic accessibility diagnostics with the pinned axe-core engine.

The engine comes only from the App Forge recipe cache and must match its pinned digest.
HTML is rendered in David's bubblewrap Chromium (no host network); every request the page
makes is aborted, so a scan never fetches subresources, submits forms or carries cookies.
Public URLs are refused until a scoped public-target acceptance exists. No model is called.
A clean scan is evidence about automated rules only, never a compliance claim.

Revisit: when the axe-core recipe, the browser sandbox or the website_audit Job contract changes.
Last touched: 2026-11-27.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECIPE = Path("agents/factory/app_forge/axe-core.recipe.json")
MAX_HTML = 256 * 1024
MAX_NODES = 20
SECONDS = 60


class AuditError(RuntimeError):
    def __init__(self, failure_class, detail):
        super().__init__(detail)
        self.failure_class = failure_class


def recipe(root=ROOT):
    return json.loads((Path(root) / RECIPE).read_text(encoding="utf-8"))


def cache_dir(spec):
    base = Path(os.environ.get("TTROS_APP_FORGE_CACHE") or Path.home() / ".local/share/ttros/app-forge")
    return base / f"{spec['package']['name']}-{spec['package']['version']}"


def engine(root=ROOT):
    """Return (source, identity) for the installed engine, or refuse if it is missing or altered."""
    spec = recipe(root)
    path = cache_dir(spec) / Path(spec["engine"]).name
    try:
        data = path.read_bytes()
    except OSError:
        raise AuditError("engine_unavailable", f"{spec['recipe']} is not installed; run app_forge install.") from None
    digest = hashlib.sha256(data).hexdigest()
    if digest != spec["files"][spec["engine"]]:
        raise AuditError("engine_integrity", f"{path.name} does not match the pinned recipe digest.")
    return data.decode("utf-8"), {"name": spec["package"]["name"], "version": spec["package"]["version"],
                                  "commit": spec["source"]["commit"], "sha256": digest}


def _normalize(raw):
    def nodes(rule):
        rows = sorted(({"target": node.get("target") or [], "html": (node.get("html") or "")[:300],
                        "summary": (node.get("failureSummary") or "")[:400]} for node in rule.get("nodes") or []),
                      key=lambda row: json.dumps(row["target"]))
        return rows[:MAX_NODES], max(0, len(rows) - MAX_NODES)

    def rules(kind):
        out = []
        for rule in sorted(raw.get(kind) or [], key=lambda rule: rule["id"]):
            shown, hidden = nodes(rule)
            out.append({"id": rule["id"], "impact": rule.get("impact") or "", "help": rule.get("help") or "",
                        "help_url": rule.get("helpUrl") or "",
                        "tags": sorted(tag for tag in rule.get("tags") or [] if tag.startswith(("wcag", "best-practice"))),
                        "nodes": shown, "nodes_truncated": hidden})
        return out

    return {"violations": rules("violations"), "incomplete": rules("incomplete"),
            "passes": len(raw.get("passes") or []), "inapplicable": len(raw.get("inapplicable") or [])}


def digest(findings):
    return hashlib.sha256(json.dumps(findings, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def audit(html, *, root=ROOT, label="inline"):
    if not isinstance(html, str) or not html.strip():
        raise AuditError("input_invalid", "Provide the HTML document to audit.")
    if len(html.encode("utf-8")) > MAX_HTML:
        raise AuditError("input_too_large", f"HTML exceeds {MAX_HTML // 1024} KiB.")
    source, identity = engine(root)
    from playwright.sync_api import Error, sync_playwright

    from aos_david_browser import SLOTS, sandboxed_chromium

    if not SLOTS.acquire(timeout=SECONDS):
        raise AuditError("browser_busy", "Browser capacity is busy.")
    started = time.monotonic()
    blocked = []
    try:
        with tempfile.TemporaryDirectory(prefix="a11y-audit-") as private, sync_playwright() as driver:
            wrapper, profile = sandboxed_chromium(driver, Path(private), seconds=SECONDS)
            context = driver.chromium.launch_persistent_context(
                str(profile), executable_path=str(wrapper), chromium_sandbox=True, headless=True, timeout=15000,
                viewport={"width": 1280, "height": 800}, accept_downloads=False, service_workers="block",
                bypass_csp=True, env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
                args=["--disable-gpu", "--disable-extensions", "--disable-external-intent-requests"])
            try:
                context.set_default_timeout(20000)

                def deny(route):
                    blocked.append(route.request.url[:200])
                    route.abort()

                context.route("**/*", deny)
                context.route_web_socket("**/*", lambda ws: ws.close())
                page = context.pages[0]
                page.set_content(html, wait_until="domcontentloaded")
                page.add_script_tag(content=source)
                raw = page.evaluate("async () => JSON.parse(JSON.stringify(await axe.run(document, "
                                    "{resultTypes: ['violations', 'incomplete']})))")
                version = page.evaluate("() => axe.version")
            finally:
                context.close()
    except Error as exc:
        raise AuditError("browser_failed", f"Chromium audit failed: {str(exc)[:300]}") from None
    finally:
        SLOTS.release()
    if version != identity["version"]:
        raise AuditError("engine_integrity", "The loaded engine reported an unexpected version.")
    findings = _normalize(raw)
    return {"target": {"kind": "html", "label": str(label)[:120],
                       "sha256": hashlib.sha256(html.encode("utf-8")).hexdigest()},
            "engine": identity, "findings": findings, "findings_sha256": digest(findings),
            "summary": {"violations": len(findings["violations"]), "incomplete": len(findings["incomplete"]),
                        "violation_nodes": sum(len(r["nodes"]) + r["nodes_truncated"] for r in findings["violations"])},
            "blocked_requests": sorted(set(blocked))[:20], "elapsed_seconds": round(time.monotonic() - started, 3),
            "limits": recipe(root)["limits"]}


def acceptance(root=ROOT):
    """Run the recipe's failing and corrected fixtures twice; verified only if both behave and repeat."""
    folder = Path(root) / RECIPE.parent / "fixtures"
    expected = {"failing.html": {"image-alt", "label", "button-name"}, "corrected.html": set()}
    runs, problems = [], []
    for name, wanted in expected.items():
        html = (folder / name).read_text(encoding="utf-8")
        first, second = audit(html, root=root, label=name), audit(html, root=root, label=name)
        found = {rule["id"] for rule in first["findings"]["violations"]}
        if not wanted <= found or (not wanted and found):
            problems.append(f"{name}: expected violations {sorted(wanted)}, found {sorted(found)}")
        if first["findings_sha256"] != second["findings_sha256"]:
            problems.append(f"{name}: repeated scans differ")
        runs.append({"fixture": name, "violations": sorted(found), "digests": [first["findings_sha256"],
                     second["findings_sha256"]], "blocked_requests": first["blocked_requests"]})
    return {"pass": not problems, "problems": problems, "runs": runs, "engine": engine(root)[1]}


def markdown(report):
    lines = [f"# Accessibility audit: {report['target']['label']}", "",
             f"Engine {report['engine']['name']} {report['engine']['version']} (sha256 {report['engine']['sha256'][:16]}).",
             f"Violations {report['summary']['violations']}, incomplete (manual review) {report['summary']['incomplete']}.",
             f"Findings digest `{report['findings_sha256']}`.", "", report["limits"], ""]
    for rule in report["findings"]["violations"]:
        lines.append(f"- **{rule['id']}** ({rule['impact']}): {rule['help']}. {rule['help_url']}")
        lines += [f"  - `{' '.join(map(str, node['target']))}`" for node in rule["nodes"]]
    if report["findings"]["incomplete"]:
        lines += ["", "Needs manual review: " + ", ".join(rule["id"] for rule in report["findings"]["incomplete"])]
    return "\n".join(lines) + "\n"


def run_job(root, job):
    """Job executor for kind "website_audit", action "accessibility". Deterministic; no model."""
    import aos_jobs

    usage = {"model_calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "exact": True}
    args = job["executor"].get("input") or {}
    try:
        if args.get("url"):
            raise AuditError("public_target_not_enabled",
                             "Public URL audits need a scoped public-target acceptance; submit the HTML instead.")
        report = audit(args.get("html"), root=root, label=args.get("label") or "inline")
    except AuditError as exc:
        return {"status": "refused" if exc.failure_class.startswith(("public", "input")) else "failed",
                "failure_class": exc.failure_class, "detail": str(exc), "output": None, "usage": usage,
                "artifacts": [], "proposed_effects": []}
    artifacts = [aos_jobs.write_job_artifact(root, job["id"], "accessibility-audit.json", json.dumps(report, indent=1) + "\n"),
                 aos_jobs.write_job_artifact(root, job["id"], "accessibility-audit.md", markdown(report))]
    summary = f"{report['summary']['violations']} violations, {report['summary']['incomplete']} need manual review"
    return {"status": "ok", "agent": "website_audit.accessibility", "output": {
                "summary": summary, "counts": report["summary"], "findings_sha256": report["findings_sha256"],
                "violations": [rule["id"] for rule in report["findings"]["violations"]], "engine": report["engine"]},
            "summary": summary, "usage": usage, "artifacts": artifacts, "proposed_effects": []}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("html_file", type=Path)
    parser.add_argument("--request-id", required=True, help="stable dedupe key for the Job")
    parser.add_argument("--root", type=Path, default=Path(os.environ.get("AOS_ROOT") or ROOT))
    args = parser.parse_args(argv)
    import aos_jobs

    html = args.html_file.read_text(encoding="utf-8")
    job, created = aos_jobs.create(args.root, conversation_id="website-audit-cli", topic="Accessibility audit",
                                   request=f"Accessibility audit of {args.html_file.name}",
                                   executor={"kind": "website_audit", "action": "accessibility",
                                             "input": {"html": html, "label": args.html_file.name}},
                                   dedupe_key=args.request_id)
    if created:
        job = aos_jobs.execute(args.root, job["id"])
    print(json.dumps({"id": job["id"], "replayed": not created, "status": job["status"],
                      "failure_class": job.get("failure_class"), "result": job.get("result"),
                      "artifacts": job.get("artifacts")}, indent=1))
    return 0 if job["status"] == "done" else 1


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    raise SystemExit(main())
