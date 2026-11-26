# Website work orders

Revisit when the website registry, preparation API or executor changes. Last touched: 2026-11-16.

Use **Websites & Digital → Prepare a website change** in the existing Dashboard.
Select a registered project; enter the request, an unused `feature/` branch, exact
repository-relative affected paths, existing source/style files and acceptance
checks (`path :: exact expected text`, one per line). Save an incomplete draft or
prepare a work order. Blockers appear beside the saved record. **Revise as new work
order** preserves the old evidence and creates a fresh snapshot when you prepare it.
Preparation never starts a worker or calls a model. **Queue prepared change** is a
separate action and uses the existing `aos_jobs` website executor and runner.

Draft means saved incomplete work. Prepared means required inputs and the local
Git/source snapshot pass deterministic checks; development has not passed yet.
Blocked means a required input, Git state, command, scope or evidence check failed.
Validated means the canonical job completed with passing site tests, exact text
acceptance checks, unchanged final review commit, a local preview and verified
artifact hashes. The job detail links the branch, diff, test log, preview, receipt
and artifact paths. Validation is evidence of that local build, not external UAT.
Released remains false and publishing stays held: this workflow contains no
push, release, deploy or publish operation, even if an input requests one.

The project registry remains `context/website_projects.json`. Preparation requires
an absolute Linux-native Git top-level checkout, explicit `client_scope`,
`base_ref`, `allowed_paths`, at least one local `icm_files` brand/style/instruction
file, a runnable `test_command`, and `preview.dir`/`preview.entry`. A preview build
command is optional for static sources. Commands are existing project configuration,
not request-supplied shell text, and must be reviewed as local tests/builds when a
project is registered. Source and asset files are hashed; the full project record
and Git baseline are fingerprinted. Source Brain pointers still pass the existing
scope registry. No additional permission or publication authority is created.

Optional website registry fields:

- `required_assets`: repository-relative files that must exist before preparation.
- `required_environment`: required environment variable names; only presence is
  checked, with no values saved. This does not verify external account validity.
- `external_requirements`: unverified hosting, UAT or other external steps displayed
  separately from local validation.
- `preview.url_prefix`: optional public URL directory corresponding to `preview.dir`
  (for example `/guides/example/`). Local HTML/CSS asset references under that exact
  prefix are made relative in the sandboxed review copy. Sources stay unchanged;
  missing assets, symlinks and traversal are refused. External links retain their
  original values. This supports a local static subtree preview from a larger site;
  it does not claim the entire site's runtime or external integrations were tested.
- `preview.max_file_bytes`: optional per-file review budget, default 2,000,000 and
  capped at 8,000,000 bytes. The existing 40-file limit and an 80,000,000-byte total
  ceiling remain enforced. Referenced local resources omitted by a file/type/size
  limit block validation; older completed jobs are checked on projection as well.

Preparation refuses dirty checkout state, HEAD differing from the registered base,
a locally known behind-upstream state and an occupied target branch. It fetches
nothing. A locally ahead baseline is allowed. Revalidation happens at queue time
and again before the existing worker starts. Runtime artifacts stay under
`results/website/`; symlink/traversal output redirection is refused. Tests/builds
must leave tracked source unchanged after the review commit. Generated output
belongs in the site's ignored build directory. Preview copying retains existing
file/size limits, hash verification and sandboxed serving. Exact text checks are
intentionally deterministic; visual quality and human acceptance remain review work.

The existing David/free-text website API remains compatible. Its older requests do
not acquire the new `validated` readiness label without a prepared specification.
The coding worker's provider/session contract is unchanged. Preparing a real order
is not evidence that its coding worker can authenticate; unavailable worker auth
must be reported by that worker. This implementation does not reuse T3 credentials
or create an alternate execution route.

The executor also accepts a local change already present in its job-owned worktree.
Its step explicitly records that no coding worker was invoked in that attempt. This
can validate human/session-authored work with the real project tests and build;
it is not evidence of autonomous coding-worker authentication or execution.

Reproduce the full offline scenario from this worktree (the existing backend venv
provides the test dependencies):

```bash
cd /home/operator/ttros-worktrees/website-ops-20261116
/opt/ttros-core/dashboard/backend/.venv/bin/python -m tests.website_readiness_demo
```

It creates a unique `/tmp/ttros-website-readiness-*` tree, synthetic Git site and
fixture-scoped records; sends inputs through the real API in process; allocates
and executes one fixture job through `aos_jobs`; builds and verifies the local
preview; and prints its work order, receipt, preview and workflow-output paths.
The `workflow-output.json` is the same projection the Websites view reads. No
server, port, tunnel, network connector, model or production queue is used. The
output tree is retained for inspection. All data is labelled synthetic. No client
repository or real business/Brain record is modified.

For an already registered project, a narrow CLI prepares and inspects the same
records without starting a worker:

```bash
python3 tools/aos_website_prepare.py --root /path/to/isolated/runtime prepare /path/to/request.json
python3 tools/aos_website_prepare.py --root /path/to/isolated/runtime show prep-REPLACE_WITH_RETURNED_ID
python3 tools/aos_website_prepare.py --root /path/to/isolated/runtime list
```

`request.json` contains `project`, `repository`, `client_scope`, `request`, `branch`,
`affected_paths`, `source_references`, and
`acceptance_checks: [{"path": "site/index.html", "contains": "Agreed headline"}]`.
The demo saves a complete example. `prepare --draft` saves incomplete inputs.
The explicit `queue <id>` subcommand allocates the existing job but never launches
an executor itself. Production scheduling remains the existing runtime's concern.

The only shared backend integration seam is the two-line router mount in
`dashboard/backend/main.py`; all handlers live in `website_readiness.py`. No new
runner or scheduler is installed. Production's EXCO record still has no registered
Linux checkout. A read-only snapshot of the real Windows EXCO development site was
validated in a Linux scratch runtime with its actual lint and Next.js production
build, a session-authored guide edit, verified preview assets and receipts. See
`proofs/website_real_local_20261116/receipt.md`. Coding-worker authentication,
hosting, DNS, deployment and publication remain unverified.

## Durable local EXCO execution (2026-11-16)

The local-execution branch registers the existing `default-site` record at
`/home/operator/ttros-projects/default-site`, pinned to its scoped snapshot commit.
Original Git history and GitHub origin are retained. This is a manual snapshot of
112 inspected source/assets/config/instruction files from the dirty Windows
redesign, not a synchronized mirror. Other tracked reference documents retain
committed versions and untracked Windows documents are excluded. Never merge job
changes back into that redesign implicitly. Reconcile explicitly with the operator
before refreshing the snapshot; prepare new orders after any registry/base change.

The optional `dependency_cache` record names an operator-installed Linux-native
`node_modules` directory and the SHA256 of its corresponding `package-lock.json`.
Preparation and execution reject a changed lockfile or escaping dependency symlink.
Execution copies dependencies into each job worktree before worker/test/build;
it never installs packages or shares a writable dependency link with the source.
This is a local dependency cache, not a package-integrity verifier or sandbox.
It consumes approximately 574 MB per fresh EXCO worktree. Existing caches must be
reviewed/reinstalled when dependencies change. No network suppression is implied:
the offline acceptance uses `unshare -Urn` to enforce it.

Only `public/guides/4-prompt-project-workflow/index.html` is enabled for the first
acceptance test. The local guide preview is sandboxed; Next's actual application
build is separately verified. It does not verify live forms or all dynamic routes.
The proof driver under `proofs/website_local_execution_20261116/` uses the branch's
actual project record, a scratch AOS_ROOT and the canonical aos_jobs executor.
It blocks coding-model invocation and retains job worktrees and receipts under
`/home/operator/ttros-worktrees/website-local-offline-20261116/`.
Production main/registry/service remain unchanged until a separately authorized
operator integrates the validated commit. Do not run the proof against live AOS_ROOT.
