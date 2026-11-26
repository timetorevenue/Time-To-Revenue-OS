# David local CLI

`david "request"` calls the existing backend at `http://127.0.0.1:8010`:
one `POST /api/dashboard/ask-david`, followed by
`GET /api/dashboard/ask-david/jobs/{job_id}` if the backend returns a job.
The CLI contains no model runner, credentials, provider fallback or authorization
policy. The backend retains its existing permission checks and subscription-backed
workbench. The Telegram bridge is unchanged.

```sh
david "Your request here"
printf '%s\n' 'Your request here' | david
david - < request.txt
david --json "Your request here"
david --job DJ-20261114T050000Z-0123abcd
```

Job IDs print to stderr as soon as acknowledged. Final answers and backend-reported
token usage print to stdout; `--json` preserves the final API fields, with the job ID
added for asynchronous results. Inline results may not include a job ID because the
API does not return one. Missing usage is reported as unavailable, never estimated.

The default total wait is 600 seconds, each HTTP call is limited to 30 seconds, and
polls are one second apart. `--timeout`, `--http-timeout` and `--poll-interval` adjust
these bounds. A local timeout or Ctrl-C does not cancel backend work. Poll failures
never submit another POST. Resume a known job with `--job`; if the initial POST's
response was lost, check the backend's existing jobs before submitting again.

Exit codes: 0 success, 2 invalid input, 3 HTTP failure, 4 connection failure,
5 timeout, 6 failed/interrupted job or unsuccessful result, 7 invalid backend
response, 130 local interruption. Transport uses loopback directly, without proxies
or redirects. Requests are limited to the backend's 8,000 UTF-8 byte maximum.

The installed `/home/operator/.local/bin/david` symlinks to this repository's executable
`tools/david_cli.py`. Before replacing any existing installation, inspect `type -a
david`, `command -V david`, aliases/functions and symlink targets. Stop on an unrelated
owner or service dependency. The retired Hermes wrapper's original bytes are preserved
at `workflows/queue_artifacts/david_cli_2026-11-13/installed_david_before.txt`.
Other launchers remain untouched. This Python file is also runnable directly.

Focused verification, without contacting a live backend:

```sh
python3 -m unittest -v tests.test_david_cli
```
