#!/usr/bin/env python3
"""Local CLI for the existing Ask David API; sends once and only polls thereafter.

Exit codes: 2 input, 3 HTTP, 4 transport, 5 timeout, 6 failed/interrupted job,
7 invalid response, 130 user interruption. No agent execution or auth fallback.
Revisit when the Ask David HTTP contract changes. Last touched: 2026-11-13.
"""
import argparse
import http.client
import json
import math
import re
import socket
import sys
import time
from urllib import error, request

BASE_URL = "http://127.0.0.1:8010"
JOB_ID = re.compile(r"DJ-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}\Z")


class CLIError(Exception):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def positive(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be a finite positive number")
    return number


def http_json(opener, method, path, payload, timeout):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    req = request.Request(BASE_URL + path, data=body, method=method,
                          headers={"Content-Type": "application/json", "Accept": "application/json"})
    try:
        with opener.open(req, timeout=timeout) as response:
            data = json.load(response)
    except error.HTTPError as exc:
        raise CLIError(3, f"HTTP {exc.code}: {exc.read(1200).decode('utf-8', errors='replace')}") from exc
    except (TimeoutError, socket.timeout) as exc:
        raise CLIError(5, "HTTP request timed out") from exc
    except error.URLError as exc:
        code = 5 if isinstance(exc.reason, TimeoutError) else 4
        raise CLIError(code, f"Backend connection failed: {exc}") from exc
    except (OSError, http.client.HTTPException) as exc:
        raise CLIError(4, f"Backend connection failed: {exc}") from exc
    except (ValueError, UnicodeError) as exc:
        raise CLIError(7, "Backend returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise CLIError(7, "Backend response must be a JSON object")
    return data


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", nargs="*", help="quoted request; '-' or piped stdin also accepted")
    parser.add_argument("--job", help="resume polling an existing job; never submits a request")
    parser.add_argument("--json", action="store_true", help="print the final API object as JSON")
    parser.add_argument("--timeout", type=positive, default=600, help="total wait seconds (default 600)")
    parser.add_argument("--http-timeout", type=positive, default=30, help="per HTTP call seconds (default 30)")
    parser.add_argument("--poll-interval", type=positive, default=1, help="seconds between polls (default 1)")
    args = parser.parse_args(argv)
    if args.job and (args.request or not JOB_ID.fullmatch(args.job)):
        parser.error("--job requires a valid DJ job ID and no request")
    if not args.job:
        if args.request == ["-"] or (not args.request and not sys.stdin.isatty()):
            text = sys.stdin.read(8001)
        else:
            text = " ".join(args.request).strip()
        if not text or len(text.encode("utf-8")) > 8000:
            parser.error("request must contain 1–8,000 UTF-8 bytes")
        text = text.strip()
        if not text:
            parser.error("request must not be blank")

    job_id = args.job
    received_result = False
    # Local-only transport: no proxy, redirects, alternate provider or POST retries.
    opener = request.build_opener(request.ProxyHandler({}), NoRedirect())
    deadline = time.monotonic() + args.timeout

    def call(method, path, payload=None):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CLIError(5, "Wait deadline exceeded")
        return http_json(opener, method, path, payload, min(args.http_timeout, remaining))

    try:
        if job_id:
            print(f"Job ID: {job_id}", file=sys.stderr, flush=True)
            data = {"kind": "david_job", "job_id": job_id}
        else:
            data = call("POST", "/api/dashboard/ask-david", {"text": text})
            if data.get("kind") == "david_job":
                job_id = data.get("job_id")
                if not isinstance(job_id, str) or not JOB_ID.fullmatch(job_id):
                    raise CLIError(7, "Backend returned an invalid job ID")
                print(f"Job ID: {job_id}", file=sys.stderr, flush=True)

        if data.get("kind") == "david_job":
            while True:
                job = call("GET", f"/api/dashboard/ask-david/jobs/{job_id}")
                if job.get("job_id") != job_id:
                    raise CLIError(7, "Backend returned a different job ID")
                status = job.get("status")
                if status == "done":
                    data = job.get("result")
                    if not isinstance(data, dict):
                        raise CLIError(7, "Completed job has no result object")
                    break
                if status in {"failed", "interrupted"}:
                    raise CLIError(6, f"Job {status}: {job.get('error') or 'no detail'}")
                if status != "running":
                    raise CLIError(7, f"Unexpected job status: {status!r}")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise CLIError(5, "Wait deadline exceeded")
                time.sleep(min(args.poll_interval, remaining))

        received_result = True
        if args.json:
            print(json.dumps({**data, **({"job_id": job_id} if job_id else {})}, ensure_ascii=False))
        else:
            print(data.get("response") or data.get("reply") or data.get("output") or data.get("message") or "")
            print(data.get("token_usage_text") or "Token usage: unavailable")
        if data.get("success") is not True:
            raise CLIError(6, f"David request failed: {data.get('error') or 'success not confirmed'}")
        return 0
    except KeyboardInterrupt:
        code, message = 130, "Interrupted locally; backend work may still be running"
    except CLIError as exc:
        code, message = exc.code, str(exc)
    print(f"david: {message}. No request was retried.", file=sys.stderr)
    if received_result:
        return code
    print("Backend work may still be running; token usage unavailable for unfinished work.", file=sys.stderr)
    if job_id:
        print(f"Resume without resubmitting: david --job {job_id}", file=sys.stderr)
    else:
        print("If submission failed, acceptance is unknown; check backend jobs before submitting again.", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
