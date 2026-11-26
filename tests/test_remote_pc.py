"""Remote PC backend: identity gate, exact-action confirmation, receipts and Windows execution.

Revisit: when dashboard/backend/remote_pc.py's identity or confirmation contract changes. · Last touched: 2026-10-26.
"""

from __future__ import annotations

import base64
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import jwt
from fastapi import HTTPException
from starlette.requests import Request

from dashboard.backend import main as backend  # noqa: F401  (installs the tools/ and backend import paths)
import remote_pc


def make_request(headers: dict[str, str], method: str = "POST", path: str = "/api/remote-pc/powershell/run") -> Request:
    raw = [(key.lower().encode(), value.encode()) for key, value in headers.items()]
    return Request({"type": "http", "method": method, "path": path, "headers": raw, "query_string": b""})


TAILNET = {"x-ttros-remote-pc": "1", "tailscale-user-login": "owner.personal@example.invalid", "origin": "https://node.tailnet.example"}


class IdentityGateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [
            patch.object(remote_pc, "RECEIPTS_DIR", Path(self.tmp.name)),
            patch.object(remote_pc, "DENIED_LOG", Path(self.tmp.name) / "denied.jsonl"),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in self.patches:
            item.stop()
        self.tmp.cleanup()

    def assertDenied(self, headers, fragment):
        with self.assertRaises(HTTPException) as caught:
            remote_pc.require_operator(make_request(headers))
        self.assertEqual(caught.exception.status_code, 403)
        self.assertIn(fragment, caught.exception.detail)

    def test_tailnet_operator_is_accepted(self):
        operator = remote_pc.require_operator(make_request(TAILNET))
        self.assertEqual(operator, {"identity": "owner.personal@example.invalid", "access_path": "tailscale_serve"})

    def test_no_identity_is_refused_and_logged(self):
        self.assertDenied({"x-ttros-remote-pc": "1"}, "no verified operator identity")
        rows = (Path(self.tmp.name) / "denied.jsonl").read_text().splitlines()
        self.assertEqual(len(rows), 1)
        self.assertEqual(json.loads(rows[0])["path"], "/api/remote-pc/powershell/run")

    def test_missing_custom_header_is_refused(self):
        self.assertDenied({k: v for k, v in TAILNET.items() if k != "x-ttros-remote-pc"}, "missing Remote PC request header")

    def test_foreign_origin_is_refused(self):
        self.assertDenied({**TAILNET, "origin": "https://evil.example"}, "cross-site origin")
        self.assertDenied({**TAILNET, "sec-fetch-site": "cross-site"}, "cross-site request")

    def test_other_tailnet_identity_is_refused(self):
        self.assertDenied({**TAILNET, "tailscale-user-login": "someone@example.com"}, "identity someone@example.com (tailscale_serve) is not authorised")
        row = json.loads((Path(self.tmp.name) / "denied.jsonl").read_text().splitlines()[-1])
        self.assertEqual((row["identity"], row["access_path"]), ("someone@example.com", "tailscale_serve"))

    def test_funnel_request_is_refused(self):
        self.assertDenied({**TAILNET, "tailscale-funnel-request": "?1"}, "Funnel")

    def test_cloudflare_path_cannot_borrow_a_tailscale_header(self):
        # A request that came through the Cloudflare tunnel must carry a valid Access JWT,
        # even when it also presents a Tailscale identity header.
        self.assertDenied({**TAILNET, "cf-ray": "abc-YYZ"}, "Cloudflare Access assertion")

    def test_cloudflare_jwt_verification_rejects_forgeries(self):
        from cryptography.hazmat.primitives.asymmetric import rsa

        team_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        attacker_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        domain = "timetorevenue.cloudflareaccess.com"
        issuer = f"https://{domain}"

        class Jwks:
            def get_signing_key_from_jwt(self, _token):
                return type("Key", (), {"key": team_key.public_key()})()

        def token(key, **claims):
            body = {"iss": issuer, "aud": "dash-aud", "exp": int(time.time()) + 300, "email": "owner.personal@example.invalid", **claims}
            return jwt.encode(body, key, algorithm="RS256")

        with patch.dict(remote_pc._jwk_clients, {domain: Jwks()}), \
                patch.dict("os.environ", {"TTROS_DASHBOARD_ACCESS_AUTH_DOMAIN": domain, "TTROS_DASHBOARD_ACCESS_AUD": ""}):
            self.assertEqual(remote_pc.verify_dashboard_access_jwt(token(team_key)), "owner.personal@example.invalid")
            self.assertIsNone(remote_pc.verify_dashboard_access_jwt(token(attacker_key)))
            self.assertIsNone(remote_pc.verify_dashboard_access_jwt(token(team_key, iss="https://evil.cloudflareaccess.com")))
            self.assertIsNone(remote_pc.verify_dashboard_access_jwt(token(team_key, exp=int(time.time()) - 60)))
            self.assertIsNone(remote_pc.verify_dashboard_access_jwt(""))
            with patch.dict("os.environ", {"TTROS_DASHBOARD_ACCESS_AUD": "pinned-aud"}):
                self.assertIsNone(remote_pc.verify_dashboard_access_jwt(token(team_key)))
                self.assertEqual(remote_pc.verify_dashboard_access_jwt(token(team_key, aud="pinned-aud")), "owner.personal@example.invalid")
            headers = {"x-ttros-remote-pc": "1", "cf-ray": "1", "cf-access-jwt-assertion": token(attacker_key)}
            self.assertDenied(headers, "Cloudflare Access assertion")
            headers["cf-access-jwt-assertion"] = token(team_key, email="someone@example.com")
            self.assertDenied(headers, "not authorised")

    def test_cloudflare_verified_jwt_is_accepted(self):
        with patch.object(remote_pc, "verify_dashboard_access_jwt", return_value="owner.personal@example.invalid"):
            operator = remote_pc.require_operator(make_request({"x-ttros-remote-pc": "1", "cf-ray": "1", "cf-access-jwt-assertion": "x.y.z"}))
        self.assertEqual(operator["access_path"], "cloudflare_access")


class ConfirmationTest(unittest.TestCase):
    def test_token_binds_exact_action_and_is_single_use(self):
        digest = remote_pc._command_digest("Get-Date", 60)
        token = remote_pc.issue_confirmation("powershell", digest, "owner.personal@example.invalid")["confirmation_token"]
        with self.assertRaises(HTTPException):
            remote_pc.consume_confirmation(token, "powershell", remote_pc._command_digest("Get-Date ", 60), "owner.personal@example.invalid")
        with self.assertRaises(HTTPException):
            remote_pc.consume_confirmation(token, "powershell", remote_pc._command_digest("Get-Date", 120), "owner.personal@example.invalid")
        with self.assertRaises(HTTPException):
            remote_pc.consume_confirmation(token, "file_upload", digest, "owner.personal@example.invalid")
        remote_pc.consume_confirmation(token, "powershell", digest, "owner.personal@example.invalid")
        with self.assertRaises(HTTPException) as caught:
            remote_pc.consume_confirmation(token, "powershell", digest, "owner.personal@example.invalid")
        self.assertIn("already used", caught.exception.detail)

    def test_missing_or_expired_token_is_refused(self):
        with self.assertRaises(HTTPException):
            remote_pc.consume_confirmation("", "powershell", "d", "owner.personal@example.invalid")
        with patch.object(remote_pc, "CONFIRMATION_TTL_SECONDS", -1):
            token = remote_pc.issue_confirmation("powershell", "d", "owner.personal@example.invalid")["confirmation_token"]
        with self.assertRaises(HTTPException) as caught:
            remote_pc.consume_confirmation(token, "powershell", "d", "owner.personal@example.invalid")
        self.assertIn("expired", caught.exception.detail)


class TranslationTest(unittest.TestCase):
    def test_wrapper_carries_the_command_byte_exact(self):
        command = "'quote\"s' + \"`$dollar\"\n日本 ✓\r\nexit 3"
        wrapper = remote_pc.build_wrapper(command, "RPC-x")
        encoded = wrapper.split("FromBase64String('")[1].split("'")[0]
        self.assertEqual(base64.b64decode(encoded).decode("utf-8"), command)

    def test_clixml_is_rendered_as_text(self):
        raw = '#< CLIXML\r\n<Objs Version="1.1.0.1" xmlns="http://schemas.microsoft.com/powershell/2004/04"><S S="Error">Get-Item : nope_x000D__x000A_</S><S S="warning">careful_x000D__x000A_</S></Objs>'
        self.assertEqual(remote_pc.decode_clixml(raw), "Get-Item : nope\nWARNING: careful\n")
        self.assertEqual(remote_pc.decode_clixml("plain"), "plain")

    def test_windows_paths_stay_on_their_drive(self):
        self.assertEqual(remote_pc.windows_to_linux("C:\\Users\\Operator"), Path("/mnt/c/Users/Operator"))
        self.assertEqual(remote_pc.windows_to_linux("c:/"), Path("/mnt/c"))
        self.assertEqual(remote_pc.linux_to_windows(Path("/mnt/c/Users/Operator")), "C:\\Users\\Operator")
        self.assertEqual(remote_pc.linux_to_windows(Path("/mnt/c")), "C:\\")
        for bad in ("C:\\..\\..\\etc", "\\\\server\\share", "/etc/passwd", "relative\\path"):
            with self.assertRaises(HTTPException, msg=bad):
                remote_pc.windows_to_linux(bad)


class InteropReliabilityTest(unittest.TestCase):
    def test_missing_wsl_handler_preserves_executable_and_every_argument(self):
        executable = Path("/mnt/c/Program Files/Tailscale/tailscale.exe")
        def exists(path):
            return str(path) == "/init"
        with patch.object(remote_pc.os, "uname", return_value=type("Uname", (), {"release": "microsoft-WSL2"})()), \
                patch.object(Path, "is_file", exists):
            self.assertEqual(remote_pc._windows_argv(executable, "serve", "status", "--json"),
                             ["/init", str(executable), str(executable), "serve", "status", "--json"])
        with patch.object(Path, "is_file", return_value=True):
            self.assertEqual(remote_pc._windows_argv(executable, "status", "--json"),
                             [str(executable), "status", "--json"])

    def test_non_wsl_does_not_use_interpreter(self):
        with patch.object(remote_pc.os, "uname", return_value=type("Uname", (), {"release": "generic-linux"})()):
            self.assertEqual(remote_pc._windows_argv(Path("example.exe"), "argument"), ["example.exe", "argument"])

    def test_failed_probe_does_not_claim_host_uninstalled_or_ready(self):
        self.assertEqual(remote_pc.desktop_status({"reachable": False})["state"], "unknown")
        status = remote_pc.desktop_status({"reachable": True, "crd_service": "Running"})
        self.assertEqual(status["state"], "host_running")
        self.assertFalse(status["external_connection_verified"])
        self.assertEqual(remote_pc.desktop_status({"reachable": True, "crd_installed": True})["state"], "installed_not_running")


class FileReceiptTest(unittest.IsolatedAsyncioTestCase):
    async def test_confirmed_upload_download_receipts_and_replay_refusal(self):
        import io
        from fastapi import UploadFile
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with patch.object(remote_pc, "windows_to_linux", return_value=root), \
                    patch.object(remote_pc, "RECEIPTS_DIR", root / "receipts"):
                body = remote_pc.UploadPrepareRequest(directory="C:\\proof", filename="proof.txt", size=5)
                request = make_request(TAILNET)
                prepared = remote_pc.files_upload_prepare(body, request)
                async def upload(token):
                    return await remote_pc.files_upload(request, body.directory, body.filename, body.size,
                                                        token, UploadFile(file=io.BytesIO(b"proof")))
                with self.assertRaises(HTTPException) as refused:
                    await upload("")
                self.assertEqual(refused.exception.status_code, 409)
                self.assertFalse((root / "proof.txt").exists())
                receipt = await upload(prepared["confirmation_token"])
                self.assertEqual((root / "proof.txt").read_bytes(), b"proof")
                self.assertEqual(json.loads(remote_pc._receipt_path(receipt["receipt_id"]).read_text())["bytes"], 5)
                with self.assertRaises(HTTPException):
                    await upload(prepared["confirmation_token"])
                with patch.object(remote_pc, "windows_to_linux", return_value=root / "proof.txt"):
                    response = remote_pc.files_download(request, "C:\\proof\\proof.txt")
                download = json.loads(remote_pc._receipt_path(response.headers["X-Remote-PC-Receipt"]).read_text())
                self.assertEqual(download["action"], "file_download")
                self.assertEqual(download["bytes"], 5)


@unittest.skipUnless(remote_pc.POWERSHELL.is_file(), "Windows interop not available")
class WindowsExecutionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = patch.object(remote_pc, "RECEIPTS_DIR", Path(self.tmp.name))
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def run_confirmed(self, command: str, timeout: int = 30):
        request = make_request(TAILNET)
        body = remote_pc.PowerShellRequest(command=command, timeout_seconds=timeout)
        prepared = remote_pc.powershell_prepare(body, request)
        body.confirmation_token = prepared["confirmation_token"]
        return remote_pc.powershell_run(body, request)

    def test_run_without_confirmation_never_executes(self):
        body = remote_pc.PowerShellRequest(command="New-Item C:\\should-not-exist.txt", timeout_seconds=30)
        with patch.object(remote_pc, "execute_powershell") as execute:
            with self.assertRaises(HTTPException) as caught:
                remote_pc.powershell_run(body, make_request(TAILNET))
        self.assertEqual(caught.exception.status_code, 409)
        execute.assert_not_called()

    def test_real_run_returns_streams_exit_code_and_receipt(self):
        result = self.run_confirmed("Write-Output 'héllo ✓'; Write-Error 'bad thing'; cmd /c exit 4")
        self.assertEqual(result["exit_code"], 4)
        self.assertEqual(result["stdout"], "héllo ✓\n")
        self.assertIn("bad thing", result["stderr"])
        receipt = json.loads(Path(result["receipt_path"]).read_text(encoding="utf-8"))
        self.assertEqual(receipt["command"], "Write-Output 'héllo ✓'; Write-Error 'bad thing'; cmd /c exit 4")
        self.assertEqual(receipt["operator"]["identity"], "owner.personal@example.invalid")

    def test_non_terminating_error_exits_one(self):
        self.assertEqual(self.run_confirmed("Get-Item C:\\ttros-remote-pc-missing")["exit_code"], 1)
        self.assertEqual(self.run_confirmed("$x = 2\nif ($x -eq 2) { 'two' }")["exit_code"], 0)
