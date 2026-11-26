"""Mocked HTTP contract tests; no models, backend changes or live submissions."""
import contextlib
import http.client
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import david_cli as cli

JOB = "DJ-20261114T050000Z-0123abcd"
ACK = {"success": True, "kind": "david_job", "job_id": JOB, "status": "running"}
RESULT = {"success": True, "response": "done", "token_usage_text": "Token usage: input 12, output 3, total 15"}


class DavidCLITests(unittest.TestCase):
    def run_cli(self, responses, args=None, stdin="", clock=None):
        self.requests = []
        responses = iter(responses)
        def open_response(req, **kwargs):
            self.requests.append(req)
            response = next(responses)
            if isinstance(response, BaseException):
                raise response
            raw = response if isinstance(response, bytes) else json.dumps(response).encode()
            return io.BytesIO(raw)
        self.out, self.err = io.StringIO(), io.StringIO()
        with contextlib.ExitStack() as stack:
            opener = stack.enter_context(patch.object(cli.request, "build_opener")).return_value
            opener.open.side_effect = open_response
            stack.enter_context(patch.object(sys, "stdin", io.StringIO(stdin)))
            stack.enter_context(contextlib.redirect_stdout(self.out))
            stack.enter_context(contextlib.redirect_stderr(self.err))
            stack.enter_context(patch.object(cli.time, "sleep"))
            if clock:
                stack.enter_context(patch.object(cli.time, "monotonic", side_effect=clock))
            return cli.main(["quoted request"] if args is None else args)

    def methods(self):
        return [req.method for req in self.requests]

    def test_immediate_success_and_reported_usage(self):
        self.assertEqual(self.run_cli([RESULT]), 0)
        self.assertEqual(self.methods(), ["POST"])
        self.assertIn(RESULT["token_usage_text"], self.out.getvalue())

    def test_arguments_are_json_encoded_without_shell_evaluation(self):
        text = 'a "quoted" request\n$(touch /must-not-run); `echo nope`'
        self.assertEqual(self.run_cli([RESULT], [text]), 0)
        self.assertEqual(json.loads(self.requests[0].data), {"text": text})

    def test_stdin_and_multiple_arguments(self):
        for args, stdin, expected in [([], 'stdin "value"\n', 'stdin "value"'),
                                      (["-"], "piped", "piped"), (["two", "words"], "", "two words")]:
            with self.subTest(args=args):
                self.assertEqual(self.run_cli([RESULT], args, stdin), 0)
                self.assertEqual(json.loads(self.requests[0].data)["text"], expected)

    def test_pending_job_polls_then_returns_json_with_job_id(self):
        running = {"job_id": JOB, "status": "running"}
        done = {"job_id": JOB, "status": "done", "result": RESULT}
        self.assertEqual(self.run_cli([ACK, running, done], ["--json", "request"]), 0)
        self.assertEqual(self.methods(), ["POST", "GET", "GET"])
        self.assertEqual(self.requests[1].full_url, cli.BASE_URL + "/api/dashboard/ask-david/jobs/" + JOB)
        self.assertIn(JOB, self.err.getvalue())
        self.assertEqual(json.loads(self.out.getvalue()), {**RESULT, "job_id": JOB})

    def test_resume_only_gets(self):
        self.assertEqual(self.run_cli([{"job_id": JOB, "status": "done", "result": RESULT}], ["--job", JOB]), 0)
        self.assertEqual(self.methods(), ["GET"])

    def test_interrupted_and_failed_jobs_are_not_resubmitted(self):
        for status in ("interrupted", "failed"):
            with self.subTest(status=status):
                self.assertEqual(self.run_cli([ACK, {"job_id": JOB, "status": status, "error": "backend stopped"}]), 6)
                self.assertEqual(self.methods(), ["POST", "GET"])
                self.assertIn("Resume without resubmitting", self.err.getvalue())

    def test_http_failure_is_not_retried(self):
        exc = HTTPError(cli.BASE_URL, 503, "unavailable", {}, io.BytesIO(b'backend unavailable'))
        self.assertEqual(self.run_cli([exc]), 3)
        self.assertEqual(self.methods(), ["POST"])
        self.assertIn("acceptance is unknown", self.err.getvalue())

    def test_connection_loss_during_polling_never_resubmits(self):
        for exc in (URLError("connection refused"), ConnectionResetError(), http.client.IncompleteRead(b'{')):
            with self.subTest(error=type(exc).__name__):
                self.assertEqual(self.run_cli([ACK, exc]), 4)
                self.assertEqual(self.methods(), ["POST", "GET"])
                self.assertIn(JOB, self.err.getvalue())

    def test_http_timeout_does_not_resubmit(self):
        for exc in (TimeoutError(), URLError(TimeoutError())):
            with self.subTest(error=type(exc).__name__):
                self.assertEqual(self.run_cli([exc]), 5)
                self.assertEqual(self.methods(), ["POST"])

    def test_poll_deadline_does_not_resubmit(self):
        self.assertEqual(self.run_cli([ACK, {"job_id": JOB, "status": "running"}],
                                      ["--timeout", "1", "request"], clock=[0, .1, .2, 2]), 5)
        self.assertEqual(self.methods(), ["POST", "GET"])

    def test_keyboard_interrupt_keeps_job_id(self):
        self.assertEqual(self.run_cli([ACK, KeyboardInterrupt()]), 130)
        self.assertEqual(self.methods(), ["POST", "GET"])
        self.assertIn(JOB, self.err.getvalue())

    def test_invalid_json_and_non_object_are_errors(self):
        for response in (b"not json", [], b'{"success":'):
            with self.subTest(response=response):
                self.assertEqual(self.run_cli([response]), 7)
                self.assertEqual(self.methods(), ["POST"])

    def test_job_contract_validation(self):
        for response in ({"job_id": JOB, "status": "unknown"},
                         {"job_id": "another", "status": "done", "result": RESULT},
                         {"job_id": JOB, "status": "done", "result": None}):
            with self.subTest(response=response):
                self.assertEqual(self.run_cli([ACK, response]), 7)
                self.assertEqual(self.methods(), ["POST", "GET"])
        self.assertEqual(self.run_cli([{**ACK, "job_id": "../escape"}]), 7)
        self.assertEqual(self.methods(), ["POST"])

    def test_failed_immediate_result_preserves_reported_usage(self):
        self.assertEqual(self.run_cli([{**RESULT, "success": False, "error": "failure"}]), 6)
        self.assertIn(RESULT["token_usage_text"], self.out.getvalue())
        self.assertNotIn("acceptance is unknown", self.err.getvalue())

    def test_missing_usage_is_unavailable_not_zero(self):
        self.assertEqual(self.run_cli([{"success": True, "response": "ok"}]), 0)
        self.assertIn("Token usage: unavailable", self.out.getvalue())

    def test_invalid_input_makes_no_requests(self):
        for args, stdin in [(["--job", "../escape"], ""), (["--job", JOB, "request"], ""),
                            (["--timeout", "nan", "request"], ""), (["--poll-interval", "0", "x"], ""),
                            ([], ""), (["x" * 8001], ""), ([], "a" * 8001), ([], "  ")]:
            with self.subTest(args=str(args)[:50]):
                with self.assertRaises(SystemExit) as exc:
                    self.run_cli([], args, stdin)
                self.assertEqual(exc.exception.code, 2)
                self.assertEqual(self.requests, [])

    def test_redirect_is_refused(self):
        self.assertIsNone(cli.NoRedirect().redirect_request(None, None, 302, "", {}, "https://elsewhere"))


if __name__ == "__main__":
    unittest.main()
