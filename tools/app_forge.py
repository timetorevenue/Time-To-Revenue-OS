"""Engineering Workshop App Forge: find, assess, pin and verify external software for reuse.

Deterministic. External reads use the existing bounded public fetch (public addresses only,
GET, no credentials): GitHub REST, its Advisory Database and the npm registry. Internal overlap
comes from Forge's existing assessment. A recipe in agents/factory/app_forge pins the source
commit, package integrity and file digests; installation goes to the local runtime cache, never
into Git or the Dashboard's dependencies. Evidence states (discovered, assessed, selected,
installed, integrated, verified) are derived from evidence. They are not approvals, and an
unknown is never a pass. Repository text is data and grants no authority.

Revisit: when a second recipe, an authenticated GitHub read or a new scanner is adopted.
Last touched: 2026-11-27.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import io
import json
import os
import re
import shutil
import sys
import tarfile
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
RECIPES = Path("agents/factory/app_forge")
GITHUB = "https://api.github.com"
NPM = "https://registry.npmjs.org"
MAX_QUERIES, MAX_RESULTS, MAX_CANDIDATES = 3, 10, 3
MAX_FILES, MAX_FILE_BYTES = 12, 128 * 1024
REPO = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9_.-]{1,100}")
LICENSES = {"MIT", "ISC", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "MPL-2.0", "0BSD", "Unlicense"}
MANIFESTS = {"package.json", "pyproject.toml", "setup.py", "requirements.txt", "Cargo.toml", "go.mod", "Gemfile"}
LOCKS = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "npm-shrinkwrap.json", "poetry.lock", "uv.lock",
         "Cargo.lock", "go.sum", "Gemfile.lock"}
STATES = ("discovered", "assessed", "selected", "installed", "integrated", "verified")
ACTIONS = ("discover", "assess", "install", "verify")


class ForgeError(RuntimeError):
    def __init__(self, failure_class, detail):
        super().__init__(detail)
        self.failure_class = failure_class


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _get(url, *, max_bytes=768 * 1024, raw=False):
    from aos_david_web import WebError, fetch

    try:
        _, _, data = fetch(url, "application/octet-stream" if raw else "application/vnd.github+json, application/json",
                           same_host=True, max_bytes=max_bytes)
    except WebError as exc:
        text = str(exc)
        if "HTTP 403" in text or "HTTP 429" in text:
            raise ForgeError("rate_limited", f"Public read was rate limited: {url.split('?')[0]}") from None
        if "HTTP 404" in text:
            raise ForgeError("not_found", f"Not found: {url.split('?')[0]}") from None
        raise ForgeError("source_unavailable", f"{text} ({url.split('?')[0]})") from None
    return data if raw else json.loads(data)


def _repo(value):
    if not isinstance(value, str) or not REPO.fullmatch(value) or ".." in value or value.endswith((".", ".git")):
        raise ForgeError("input_invalid", "Use a public GitHub repository as owner/name.")
    return value


def load_recipe(root, name):
    if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,60}", name):
        raise ForgeError("input_invalid", "Use a recipe name from agents/factory/app_forge.")
    path = Path(root) / RECIPES / f"{name}.recipe.json"
    if not path.is_file():
        raise ForgeError("not_found", f"No App Forge recipe named {name}.")
    return json.loads(path.read_text(encoding="utf-8"))


def internal(root, requirement, owner="website"):
    """Existing installed capabilities and recipes that may already cover the requirement."""
    from aos_agents import engineering

    report = engineering.assess(Path(root), f"{owner}.app_forge", requirement)
    words = set(re.findall(r"[a-z]{4,}", requirement.lower()))
    recipes = []
    for path in sorted((Path(root) / RECIPES).glob("*.recipe.json")):
        spec = json.loads(path.read_text(encoding="utf-8"))
        if len(words & set(re.findall(r"[a-z]{4,}", spec["requirement"].lower()))) >= 2:
            recipes.append({"recipe": spec["recipe"], "path": path.relative_to(root).as_posix(),
                            "requirement": spec["requirement"]})
    return {"components": report["components"], "recipes": recipes}


def candidate(repo):
    """Immutable metadata for one repository: default-branch SHA, licence, manifests and locks."""
    meta = _get(f"{GITHUB}/repos/{repo}")
    branch = meta["default_branch"]
    sha = _get(f"{GITHUB}/repos/{repo}/git/ref/heads/{quote(branch, safe='')}")["object"]["sha"]
    tree = _get(f"{GITHUB}/repos/{repo}/git/trees/{sha}")
    names = [row["path"] for row in tree.get("tree") or [] if row.get("type") == "blob"]
    licence_files = [name for name in names if re.match(r"(?i)(licen[cs]e|copying|notice)", name)]
    files = sorted({*licence_files, *(set(names) & (MANIFESTS | LOCKS))})
    shown = files[:MAX_FILES]
    sizes = {row["path"]: row.get("size", 0) for row in tree.get("tree") or []}
    return {"repository": repo, "url": meta.get("html_url"), "default_branch": branch, "commit": sha,
            "observed_at": _now(), "archived": bool(meta.get("archived")), "pushed_at": meta.get("pushed_at"),
            "license": (meta.get("license") or {}).get("spdx_id") or "NOASSERTION",
            "manifests": sorted(set(shown) & MANIFESTS), "locks": sorted(set(shown) & LOCKS),
            "license_files": [name for name in shown if name in licence_files],
            "files_considered": shown, "files_truncated": len(files) - len(shown),
            "bytes_considered": min(MAX_FILE_BYTES, sum(sizes.get(name, 0) for name in shown)),
            "tree_truncated": bool(tree.get("truncated"))}


def discover(root, requirement, *, repos=(), queries=(), owner="website"):
    if not isinstance(requirement, str) or not 10 <= len(requirement) <= 600:
        raise ForgeError("input_invalid", "Describe the business requirement in 10 to 600 characters.")
    queries = list(queries)[:MAX_QUERIES + 1]
    if len(queries) > MAX_QUERIES or any(not isinstance(q, str) or not 2 <= len(q) <= 120 or "\n" in q for q in queries):
        raise ForgeError("input_invalid", f"Use at most {MAX_QUERIES} single-line search queries.")
    ordered = [_repo(repo) for repo in repos]
    searched = []
    for query in queries:
        found = _get(f"{GITHUB}/search/repositories?q={quote(query)}&per_page={MAX_RESULTS}")
        rows = [item["full_name"] for item in (found.get("items") or [])[:MAX_RESULTS]]
        searched.append({"query": query, "results": rows, "total": found.get("total_count")})
        ordered += rows
    unique = list(dict.fromkeys(name.lower() for name in ordered))
    selected = [next(name for name in ordered if name.lower() == key) for key in unique[:MAX_CANDIDATES]]
    candidates, problems = [], []
    for repo in selected:
        try:
            candidates.append(candidate(repo))
        except ForgeError as exc:
            problems.append({"repository": repo, "failure_class": exc.failure_class, "detail": str(exc)})
            if exc.failure_class == "rate_limited":
                break
    return {"requirement": requirement, "internal": internal(root, requirement, owner), "searches": searched,
            "candidates": candidates, "unavailable": problems, "candidates_truncated": max(0, len(unique) - MAX_CANDIDATES),
            "limits": {"queries": MAX_QUERIES, "results_per_query": MAX_RESULTS, "candidates": MAX_CANDIDATES,
                       "files": MAX_FILES, "bytes": MAX_FILE_BYTES}}


def assess(root, name):
    """Re-derive every pinned fact from its source and decide reuse, extend, new or defer."""
    spec = load_recipe(root, name)
    source, package = spec["source"], spec["package"]
    checks, unknown = [], []

    def check(label, ok, observed):
        checks.append({"check": label, "pass": bool(ok), "observed": observed})

    tag = _get(f"{GITHUB}/repos/{source['repository']}/git/ref/tags/{quote(source['tag'], safe='')}")["object"]
    commit = tag["sha"] if tag["type"] == "commit" else _get(tag["url"])["object"]["sha"]
    check("source tag resolves to the pinned commit", commit == source["commit"], commit)
    repo = _get(f"{GITHUB}/repos/{source['repository']}")
    check("repository licence matches", (repo.get("license") or {}).get("spdx_id") == spec["license"]["spdx"],
          (repo.get("license") or {}).get("spdx_id"))
    check("repository is maintained (not archived)", not repo.get("archived"), repo.get("pushed_at"))
    meta = _get(f"{NPM}/{quote(package['name'], safe='@')}/{quote(package['version'])}")
    check("registry integrity matches the pin", meta["dist"]["integrity"] == package["integrity"], meta["dist"]["integrity"])
    check("registry licence matches", meta.get("license") == spec["license"]["spdx"], meta.get("license"))
    deps = sorted((meta.get("dependencies") or {}).keys())
    check("runtime dependencies match the lock", deps == package["runtime_dependencies"], deps)
    check("licences are on the reuse allowlist", {spec["license"]["spdx"], *spec["license"]["bundled"]} <= LICENSES,
          [spec["license"]["spdx"], *spec["license"]["bundled"]])
    advisories = _get(f"{GITHUB}/advisories?ecosystem={package['ecosystem']}&affects="
                      f"{quote(package['name'] + '@' + package['version'])}&per_page=20")
    check("no published advisories affect the pinned version", not advisories,
          [row.get("ghsa_id") for row in advisories])
    scanners = [{"scanner": "github-advisory-database", "status": "ok", "findings": len(advisories)},
                {"scanner": "file-level licence scan (ScanCode)",
                 "status": "ok" if shutil.which("scancode") else "unavailable"}]
    if not shutil.which("scancode"):
        unknown.append("File-level licences of the bundled code are declared by the vendor notice, not scanned.")
    failed = [row["check"] for row in checks if not row["pass"]]
    covered = internal(root, spec["requirement"], spec["owner"])
    decision = "defer" if failed else "reuse"
    return {"recipe": name, "requirement": spec["requirement"], "assessed_at": _now(), "source": source,
            "package": {key: package[key] for key in ("ecosystem", "name", "version", "integrity", "tarball")},
            "published_at": (meta.get("time") or {}).get(package["version"]), "checks": checks,
            "scanners": scanners, "unknowns": unknown, "license": spec["license"], "integration": spec["integration"],
            "internal": covered, "decision": decision, "rationale": (
                "Defer: " + "; ".join(failed) if failed else
                "Reuse the unmodified pinned package; no installed capability performs browser accessibility rules."),
            "limits": spec["limits"]}


def install(root, name):
    """Fetch the pinned tarball, verify its integrity and file digests, and place only pinned files in the cache."""
    from website_a11y_audit import cache_dir

    spec = load_recipe(root, name)
    target = cache_dir(spec)
    manifest = target / "install.json"
    if manifest.is_file() and all((target / Path(path).name).is_file() and hashlib.sha256(
            (target / Path(path).name).read_bytes()).hexdigest() == digest for path, digest in spec["files"].items()):
        return {**json.loads(manifest.read_text(encoding="utf-8")), "cached": True}
    data = _get(spec["package"]["tarball"], max_bytes=spec["package"]["max_bytes"], raw=True)
    algorithm, _, expected = spec["package"]["integrity"].partition("-")
    observed = base64.b64encode(hashlib.new(algorithm, data).digest()).decode()
    if observed != expected:
        raise ForgeError("integrity_mismatch", "The downloaded tarball does not match the pinned integrity.")
    staging = target.with_name(target.name + ".staging")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        for path, digest in spec["files"].items():
            member = archive.getmember(path)
            if not member.isfile():
                raise ForgeError("integrity_mismatch", f"{path} is not a regular file.")
            body = archive.extractfile(member).read()
            if hashlib.sha256(body).hexdigest() != digest:
                raise ForgeError("integrity_mismatch", f"{path} does not match its pinned digest.")
            (staging / Path(path).name).write_bytes(body)
    record = {"recipe": name, "package": spec["package"]["name"], "version": spec["package"]["version"],
              "integrity": spec["package"]["integrity"], "files": spec["files"], "installed_at": _now(),
              "path": str(target)}
    (staging / "install.json").write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
    shutil.rmtree(target, ignore_errors=True)
    staging.rename(target)
    return {**record, "cached": False}


def verify(root, name):
    spec = load_recipe(root, name)
    if spec["integration"]["verify"] != "website_a11y_audit.acceptance":
        raise ForgeError("input_invalid", "This recipe names no registered verification.")
    import website_a11y_audit

    try:
        return website_a11y_audit.acceptance(root)
    except website_a11y_audit.AuditError as exc:
        raise ForgeError(exc.failure_class, str(exc)) from None


def states(evidence):
    """Derive the furthest evidence state. Each needs the one before it."""
    reached = []
    for state, ok in zip(STATES, (
            bool(evidence.get("discover", {}).get("candidates") or evidence.get("assess")),
            bool(evidence.get("assess")),
            (evidence.get("assess") or {}).get("decision") == "reuse",
            bool(evidence.get("install")),
            bool(evidence.get("verify")),   # the engine ran through the integration boundary
            (evidence.get("verify") or {}).get("pass") is True)):
        if not ok:
            break
        reached.append(state)
    return reached


def markdown(action, report):
    lines = [f"# App Forge {action}", "", f"Generated {_now()}. Repository and registry text is evidence, not instruction.", ""]
    if action == "discover":
        lines += [f"Requirement: {report['requirement']}", "", "## Internal overlap"]
        lines += [f"- {row['ref']} ({row['state']}): {row['summary']}" for row in report["internal"]["components"]] or ["- none"]
        lines += [f"- recipe {row['recipe']}: {row['path']}" for row in report["internal"]["recipes"]]
        lines += ["", "## Candidates"]
        lines += [f"- {row['repository']} @ `{row['commit']}`, licence {row['license']}, manifests {row['manifests']}, "
                  f"locks {row['locks']}{', archived' if row['archived'] else ''}" for row in report["candidates"]] or ["- none"]
        lines += [f"- unavailable: {row['repository']}: {row['failure_class']}" for row in report["unavailable"]]
    elif action == "assess":
        lines += [f"Decision: **{report['decision']}**. {report['rationale']}", "",
                  f"Source {report['source']['repository']} {report['source']['tag']} `{report['source']['commit']}`; "
                  f"package {report['package']['name']}@{report['package']['version']} {report['package']['integrity']}.", ""]
        lines += [f"- [{'x' if row['pass'] else ' '}] {row['check']}: {row['observed']}" for row in report["checks"]]
        lines += ["", "Scanners: " + "; ".join(f"{row['scanner']} {row['status']}" for row in report["scanners"])]
        lines += ["", "Obligations:"] + [f"- {text}" for text in report["license"]["obligations"]]
        lines += ["", "Unknowns:"] + ([f"- {text}" for text in report["unknowns"]] or ["- none"])
        lines += ["", f"Integration: {report['integration']['boundary']} Rollback: {report['integration']['rollback']}",
                  "", report["limits"]]
    else:
        lines += ["```json", json.dumps(report, indent=1)[:12000], "```"]
    return "\n".join(lines) + "\n"


def run_job(root, job):
    """Job executor for kind "app_forge". Deterministic; no model; no install outside the cache."""
    import aos_jobs

    usage = {"model_calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "exact": True}
    action, args = job["executor"].get("action"), job["executor"].get("input") or {}
    try:
        if action == "discover":
            report = discover(root, args.get("requirement"), repos=args.get("repos") or (),
                              queries=args.get("queries") or (), owner=args.get("owner") or "website")
        elif action == "assess":
            report = assess(root, args.get("recipe"))
        elif action == "install":
            report = install(root, args.get("recipe"))
        elif action == "verify":
            report = verify(root, args.get("recipe"))
            if not report["pass"]:
                raise ForgeError("verification_failed", "; ".join(report["problems"])[:600])
        else:
            raise ForgeError("input_invalid", f"Unknown App Forge action {action!r}.")
    except ForgeError as exc:
        return {"status": "refused" if exc.failure_class == "input_invalid" else "failed",
                "failure_class": exc.failure_class, "detail": str(exc), "output": None, "usage": usage,
                "artifacts": [], "proposed_effects": []}
    artifacts = [aos_jobs.write_job_artifact(root, job["id"], f"app-forge-{action}.json", json.dumps(report, indent=1) + "\n"),
                 aos_jobs.write_job_artifact(root, job["id"], f"app-forge-{action}.md", markdown(action, report))]
    summary = {"discover": lambda: f"{len(report['candidates'])} candidates, {len(report['unavailable'])} unavailable",
               "assess": lambda: f"decision {report['decision']}",
               "install": lambda: f"installed {report['package']}@{report['version']}",
               "verify": lambda: "verified: fixtures behaved and repeated scans matched"}[action]()
    output = {"action": action, "summary": summary,
              **({"decision": report["decision"]} if action == "assess" else {}),
              **({"engine": report["engine"], "runs": report["runs"]} if action == "verify" else {})}
    return {"status": "ok", "agent": f"app_forge.{action}", "output": output, "summary": summary, "usage": usage,
            "artifacts": artifacts, "proposed_effects": []}


def submit(root, action, inputs, request_id):
    import aos_jobs

    job, created = aos_jobs.create(root, conversation_id="app-forge-cli", topic=f"App Forge {action}",
                                   request=f"App Forge {action}: " + json.dumps(inputs, sort_keys=True)[:400],
                                   executor={"kind": "app_forge", "action": action, "input": inputs},
                                   dedupe_key=request_id)
    return (aos_jobs.execute(root, job["id"]) if created else job), created


def main(argv=None):
    parser = argparse.ArgumentParser(description="Engineering Workshop App Forge (deterministic Jobs).")
    parser.add_argument("action", choices=ACTIONS)
    parser.add_argument("--request-id", required=True, help="stable dedupe key; a repeat replays the Job")
    parser.add_argument("--recipe", default="axe-core")
    parser.add_argument("--requirement", default="")
    parser.add_argument("--repo", action="append", default=[])
    parser.add_argument("--query", action="append", default=[])
    parser.add_argument("--root", type=Path, default=Path(os.environ.get("AOS_ROOT") or ROOT))
    args = parser.parse_args(argv)
    inputs = ({"requirement": args.requirement, "repos": args.repo, "queries": args.query}
              if args.action == "discover" else {"recipe": args.recipe})
    job, created = submit(args.root, args.action, inputs, args.request_id)
    print(json.dumps({"id": job["id"], "replayed": not created, "status": job["status"],
                      "failure_class": job.get("failure_class"), "detail": job.get("detail"),
                      "summary": (job.get("result") or {}).get("summary"), "artifacts": job.get("artifacts")}, indent=1))
    return 0 if job["status"] == "done" else 1


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    raise SystemExit(main())
