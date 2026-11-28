"""Turn-scoped Chromium. The renderer cannot reach the network or operator files."""
from __future__ import annotations

import re
import shlex
import shutil
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit

from aos_capabilities import CapabilityError
from aos_david_files import ARTIFACT_LIMIT, FileError
from aos_david_web import NOTICE, WebError, _url, fetch

SLOTS = threading.BoundedSemaphore(2)
ROLES = ["button", "link", "heading", "textbox", "combobox", "checkbox", "radio", "tab",
         "menuitem", "option", "dialog", "region", "table", "row", "cell", "main"]
SAFE_CLICK = {"button", "tab", "menuitem", "option", "checkbox", "radio"}
DOCUMENT_SUFFIXES = {".pdf", ".csv", ".txt", ".md", ".json", ".png", ".jpg", ".jpeg"}
NEGATION = re.compile(r"\b(?:never|do not|don['’]t)\s+(?:open|navigate|browse|click|attach|upload|download|fill|type)\b", re.I)
ACTION_PATH = re.compile(r"(?:^|[/_-])(?:delete|unsubscribe|logout|signout|purchase|checkout|confirm|submit|publish|send)(?:$|[/_.-])", re.I)


def authorized_urls(text):
    found = set()
    for candidate in re.findall(r"https?://[^\s<>\"\x60]+", text, re.I):
        try:
            found.add(_url(candidate.rstrip(".,;!?)\u005d")))
        except WebError:
            continue
    return found


def origin(url):
    return urlsplit(url).scheme, urlsplit(url).hostname


def _filename(value, fallback):
    safe = re.sub(r"[^a-zA-Z0-9._ -]", "-", value)[:115].strip(" .-")
    return safe if safe and safe[0].isalnum() else fallback


def sandboxed_chromium(driver, private, seconds=180):
    """Wrap Playwright's Chromium in bubblewrap with no host network or operator files.

    Returns (wrapper executable, profile directory). Callers own ``private``.
    """
    exe = Path(driver.chromium.executable_path)
    if not exe.is_file():
        raise CapabilityError("Install the runtime Playwright Chromium revision.")
    # DevTools control is an inherited pipe. No host network, profile, IPC
    # socket, credentials, downloads directory or local browser is mounted.
    sandbox = [shutil.which("timeout"), "--kill-after=2s", f"{int(seconds)}s", shutil.which("bwrap"),
               "--unshare-all", "--die-with-parent", "--new-session", "--proc", "/proc",
               "--dev", "/dev", "--tmpfs", "/tmp", "--tmpfs", "/dev/shm"]
    for path in ("/usr", "/lib", "/lib64", "/etc/fonts", str(exe.parent)):
        sandbox += ["--ro-bind-try", path, path]
    profile = Path(private) / "profile"
    profile.mkdir(mode=0o700)
    sandbox += ["--tmpfs", str(profile),
                "--chdir", "/tmp", "--setenv", "HOME", "/tmp", str(exe)]
    wrapper = Path(private) / "chromium"
    wrapper.write_text("#!/bin/sh\nexec " + shlex.join(sandbox) + ' "$@"\n')
    wrapper.chmod(0o700)
    return wrapper, profile


class Browser:
    def __init__(self, files, operator_text, authorize, *, retrieve=fetch):
        self.files = files
        self.operator_text = operator_text
        self.authorize = authorize
        self.retrieve = retrieve
        self.seeds = authorized_urls(operator_text)
        self.context = self.driver = self.scratch = None
        self.tabs = {}
        self.remaining = 75
        self.byte_budget = 16 * 1024 * 1024
        self.deadline = 0
        self.slot = False
        self.sealed = False
        self.navigation = None
        self.origins = {}
        self.frozen = set()
        self.last_error = ""
        self.step_count = 0
        self.cache = {}

    def _start(self):
        from playwright.sync_api import Error, sync_playwright

        self.playwright_error = Error

        if not shutil.which("bwrap") or not shutil.which("timeout"):
            raise CapabilityError("Browser isolation requires bubblewrap and timeout.")
        if not SLOTS.acquire(blocking=False):
            raise CapabilityError("Browser capacity is busy. Try again shortly.")
        self.slot = True
        self.scratch = tempfile.TemporaryDirectory(prefix="david-browser-")
        private = Path(self.scratch.name)
        try:
            self.driver = sync_playwright().start()
            wrapper, profile = sandboxed_chromium(self.driver, private)
            self.context = self.driver.chromium.launch_persistent_context(
                str(profile), executable_path=str(wrapper), chromium_sandbox=True,
                headless=True, timeout=7000, viewport={"width": 1200, "height": 800},
                accept_downloads=False, service_workers="block",
                env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
                args=["--disable-gpu", "--disable-external-intent-requests", "--disable-extensions"])
            self.context.set_default_timeout(2400)
            self.context.route("**/*", self._route)
            self.context.route_web_socket("**/*", lambda ws: ws.close())
            self.tabs["main"] = self.context.pages[0]
        except BaseException:
            self.close()
            raise

    def close(self):
        # Stopping Playwright after closing the context also closes the transport
        # when Chromium has already crashed or the watchdog ends the process.
        try:
            if self.context:
                self.context.close()
        finally:
            try:
                if self.driver:
                    self.driver.stop()
            finally:
                if self.scratch:
                    self.scratch.cleanup()
                if self.slot:
                    SLOTS.release()
                self.context = self.driver = self.scratch = None
                self.slot = False
                self.tabs.clear()
                self.cache.clear()
                self.origins.clear()
                self.frozen.clear()

    def _route(self, route):
        request = route.request
        try:
            url = _url(request.url)
            page = request.frame.page
            site = self.origins.get(page)
            document = request.resource_type == "document"
            permitted = (document and request.is_navigation_request() and request.frame == page.main_frame
                         and (page, url) == self.navigation)
            asset = (not document and request.frame == page.main_frame and origin(url) == site
                     and request.resource_type in {"script", "stylesheet", "image", "font", "xhr", "fetch"})
            if (self.sealed or page in self.frozen or request.method != "GET" or not (permitted or asset)
                    or time.monotonic() > self.deadline or not self.remaining or self.byte_budget <= 0):
                route.abort()
                return
            _safe_read_url(url)
            if permitted:
                self.navigation = None
            if url not in self.cache:
                self.remaining -= 1
                final, headers, data = self.retrieve(url, "*/*", same_host=True,
                                                     max_bytes=min(self.byte_budget, 2 * 1024 * 1024),
                                                     allow_url=_safe_read_url)
                content_type = headers.get("Content-Type", "application/octet-stream")
                if origin(final) != site:
                    if not (document and origin(final) == ("https", site[1])):
                        raise WebError("Browser redirect left the scoped origin.")
                    self.origins[page] = origin(final)
                if document and ("attachment" in headers.get("Content-Disposition", "").lower()
                                 or content_type.split(";")[0].lower() not in
                                 {"text/html", "text/plain", "application/xhtml+xml"}):
                    raise WebError("Use browser download for this file.")
                self.byte_budget -= len(data)
                self.cache[url] = self.cache[final] = final, content_type, data, {
                    key: val for key, val in headers.items()
                    if key.lower() in {"content-security-policy", "x-content-type-options"}
                }
            final, mime, data, security_headers = self.cache[url]
            # Fulfill the validated redirect ourselves. Chromium receives neither
            # authentication cookies nor an unvalidated Location header.
            if document and final != url:
                self.redirected = final
                route.fulfill(status=200, content_type="text/html", body=b"")
                return
            route.fulfill(status=200, content_type=mime, headers=security_headers, body=data)
        except (WebError, OSError) as exc:
            if request.resource_type == "document":
                self.last_error = str(exc)
                route.fulfill(status=502, content_type="text/plain", body="David could not retrieve this document.")
            else:
                route.abort()
        except self.playwright_error:
            # Popups and detached frames can lack a main-frame binding.
            try:
                route.abort()
            except self.playwright_error:
                pass

    def _locate(self, page, step):
        if "name" not in step:
            raise CapabilityError("Specify the exact visible accessible name or label.")
        match = (page.get_by_role(step["role"], name=step["name"], exact=True)
                 if "role" in step else page.get_by_label(step["name"], exact=True))
        if match.count() != 1 or not match.is_visible():
            raise CapabilityError("The element was missing, hidden or ambiguous. Read the page for its accessible label.")
        return match

    def _navigate(self, page, url, *, link=False):
        url = _url(url)
        if self.sealed:
            raise CapabilityError("A prepared browser session is offline for the rest of this turn.")
        if url not in self.seeds and (not link or origin(url) != origin(page.url)):
            raise CapabilityError("Open a URL from the operator's request or follow a named link within that site.")
        _safe_read_url(url)
        self.last_error = ""
        self.redirected = None
        self.frozen.discard(page)
        self.navigation, self.origins[page] = (page, url), origin(url)
        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=8500)
            if response is not None and not response.ok:
                raise CapabilityError(self.last_error or f"Navigation returned HTTP {response.status}.")
            if self.redirected:
                target, self.redirected = self.redirected, None
                self.navigation = (page, target)
                self.origins[page] = origin(target)
                response = page.goto(target, wait_until="domcontentloaded", timeout=8500)
                if response is not None and not response.ok:
                    raise CapabilityError(self.last_error or f"Navigation returned HTTP {response.status}.")
        finally:
            self.navigation = None
        return {"operation": "navigate", "url": page.url[:400], "title": page.title()[:100]}

    def _form(self, page, step, *, upload):
        self.authorize()
        if not page.url.startswith(("http://", "https://")):
            raise CapabilityError("Open the form's authorized site first.")
        match = self._locate(page, step)
        token = step["name"]
        if token.casefold() not in self.operator_text.casefold():
            raise CapabilityError("Name the field in the operator request.")
        kind = match.evaluate("e => e instanceof HTMLTextAreaElement ? 'textarea' : e.type")
        if upload:
            if (kind != "file" or not re.search(r"\b(?:upload|attach)\b", self.operator_text, re.I)):
                raise CapabilityError("An explicitly requested file input is required.")
            row, data = self.files.verified_bytes(step.get("file_id", ""))
            if (row["id"] not in self.operator_text and row["filename"] not in self.operator_text
                    and row["id"] not in self.files.attachments):
                raise CapabilityError("Name or attach the file in the operator request.")
            suffix = Path(row["filename"]).suffix.lower()
            _validate_download(suffix, data)
            self.sealed = True
            match.set_input_files({"name": row["filename"], "mimeType": "application/octet-stream", "buffer": data})
        else:
            value = step.get("value")
            if (kind not in {"email", "text", "textarea", "tel", "url", "number", "search", "select-one"}
                    or not value or value not in self.operator_text
                    or not re.search(r"\b(?:fill|prepare|enter|type|complete)\b", self.operator_text, re.I)):
                raise CapabilityError("Preparation requires a supported field and a value supplied in the request.")
            self.sealed = True
            if kind == "select-one":
                selected = match.select_option(label=value)
                if not selected:
                    raise CapabilityError("The page did not retain the requested option.")
            else:
                match.fill(value)
                if match.input_value() != value:
                    raise CapabilityError("The page did not retain the requested field value.")
        return {"operation": "upload" if upload else "fill", "status": "prepared_offline", "field": token}

    def _download(self, page, step):
        self.authorize()
        if self.sealed or not re.search(r"\b(?:download|save|retrieve)\b", self.operator_text, re.I):
            raise CapabilityError("Downloading needs a request to retrieve a file and an unsealed browser.")
        url = step.get("url")
        if not url and step.get("role") == "link":
            url = self._locate(page, step).evaluate("e => e.href")
        if not url:
            raise CapabilityError("Supply the operator's exact URL or a named file link within the site.")
        url = _url(url)
        if url not in self.seeds and (step.get("url") or origin(url) != origin(page.url)):
            raise CapabilityError("Use the operator's exact URL or a named file link within the site.")
        final, headers, data = self.retrieve(url, "*/*", same_host=True, max_bytes=ARTIFACT_LIMIT,
                                             allow_url=_safe_read_url)
        filename = _filename(Path(urlsplit(final).path).name, "download")
        suffix = Path(filename).suffix.lower()
        if not suffix:
            suffix = {"application/pdf": ".pdf", "text/plain": ".txt",
                      "text/csv": ".csv", "application/json": ".json"}.get(
                headers.get("Content-Type", "").split(";")[0].strip().lower(), "")
            filename += suffix
        _validate_download(suffix, data)
        return {"operation": "download", "source": final[:400], **self.files.store(filename, data, media=True)}

    def _step(self, step):
        action = step["op"]
        tab = step.get("tab", "main")
        if action == "download" and step.get("url"):
            return self._download(None, step)
        if self.context is None:
            self._start()
        if action == "tabs":
            return {"operation": action, "tabs": [{"tab": key, "url": page.url[:300]}
                                                  for key, page in self.tabs.items() if not page.is_closed()]}
        if action == "open_tab":
            if tab in self.tabs or len(self.tabs) >= 3:
                raise CapabilityError("Use a new tab name. The session has space for three tabs.")
            self.tabs[tab] = self.context.new_page()
            return {"operation": action, "tab": tab}
        page = self.tabs.get(tab)
        if page is None or page.is_closed():
            raise CapabilityError("That tab is unavailable. Open a fresh tab.")
        if action == "close_tab":
            page.close()
            self.frozen.discard(page)
            self.origins.pop(page, None)
            del self.tabs[tab]
            return {"operation": action, "tab": tab}
        if action == "navigate":
            return self._navigate(page, step.get("url"))
        if action == "read":
            target = self._locate(page, step) if "name" in step else page.locator("body")
            # Trim in the renderer before transporting text. Limit the accessibility
            # tree to a named element, or sample interactive semantics from the DOM.
            if "name" in step:
                return {"operation": action, "url": page.url[:400],
                        "text": target.evaluate("e => e.innerText.slice(0, 1000)"),
                        "accessibility": target.aria_snapshot(timeout=2000)[:850]}
            return {"operation": action, "url": page.url[:400], **target.evaluate("""e => ({
                text: e.innerText.slice(0, 1000),
                elements: Array.from(e.querySelectorAll('a,button,input,select,textarea,[role=tab],[role=button]'))
                    .slice(0,80).filter(n => n.getClientRects().length).slice(0,14).map(n => ({
                        role: n.getAttribute('role') || ({A:'link',BUTTON:'button',SELECT:'combobox'}[n.tagName])
                            || (['checkbox','radio'].includes(n.type) ? n.type : 'textbox'),
                        name: (n.getAttribute('aria-label') || Array.from(n.labels || []).map(l=>l.innerText).join(' ')
                            || n.innerText || n.getAttribute('placeholder') || '').trim().slice(0,80)
                    }))
                })""")}
        if action == "click":
            match = self._locate(page, step)
            if step.get("role") == "link":
                href = match.evaluate("e => e.href")
                if not href:
                    raise CapabilityError("This link has no public document destination.")
                return self._navigate(page, href, link=True)
            if (step.get("role") not in SAFE_CLICK or match.evaluate(
                    "e => Boolean(e.closest('form,a') || e.form || e.type === 'submit')")):
                raise CapabilityError("Form submission and external actions are outside this browser operation.")
            # A UI click can run arbitrary site script. Cut off every request
            # before the event and keep the page in this mode until a clean navigation.
            self.frozen.add(page)
            match.click(timeout=2000)
            return {"operation": action, "name": step["name"], "status": "clicked_locally"}
        if action == "scroll":
            page.mouse.wheel(0, step.get("pixels", 600))
            return {"operation": action}
        if action in {"fill", "upload"}:
            return self._form(page, step, upload=action == "upload")
        if action == "download":
            return self._download(page, step)
        if action == "screenshot":
            data = page.screenshot(type="jpeg", quality=74, timeout=3500, animations="disabled")
            label = _filename(step.get("name", "browser"), "browser")
            stem = label.removesuffix(".jpg").removesuffix(".jpeg")
            return {"operation": action, **self.files.store(stem + ".jpg", data, media=True)}
        raise CapabilityError("Unknown browser operation.")

    def run(self, args):
        if NEGATION.search(self.operator_text):
            raise CapabilityError("The operator request explicitly restricts browser actions.")
        self.authorize()
        try:
            from playwright.sync_api import Error
        except ImportError:
            raise CapabilityError("The runtime Playwright dependency is unavailable.") from None
        self.deadline = time.monotonic() + 13
        steps = []
        artifacts = []
        try:
            for step in args["steps"]:
                if time.monotonic() >= self.deadline or self.step_count >= 24:
                    raise CapabilityError("Browser execution reached its time or step limit.")
                self.step_count += 1
                row = self._step(step)
                steps.append(row)
                if row.get("status") == "stored":
                    artifacts.append(row)
        except (CapabilityError, FileError, WebError, OSError) as exc:
            return {"status": "partial", "steps": steps, "artifacts": artifacts, "error": str(exc), "data_notice": NOTICE}
        except Error:
            # A crashed context cannot participate in future batches. A clean
            # turn can start a new anonymous context without replaying steps.
            if self.context is not None and not self.context.pages:
                self.close()
            return {"status": "partial", "steps": steps, "artifacts": artifacts,
                    "error": self.last_error or "Browser operation failed or timed out. Read the page or open a fresh tab.",
                    "data_notice": NOTICE}
        return {"status": "ok", "steps": steps, "artifacts": artifacts, "data_notice": NOTICE}


def _safe_read_url(url):
    parts = urlsplit(url)
    if (ACTION_PATH.search(unquote(parts.path))
            or any(ACTION_PATH.search(value) for key, value in parse_qsl(parts.query)
                   if key.casefold() in {"action", "op", "operation", "intent"})):
        raise WebError("That URL appears to perform an external action.")


def _validate_download(suffix, data):
    if suffix not in DOCUMENT_SUFFIXES or not data:
        raise CapabilityError("Only PDF, PNG, JPEG, text, CSV, Markdown and JSON files are supported.")
    if suffix == ".pdf":
        valid = data.startswith(b"%PDF-")
    elif suffix == ".png":
        valid = data.startswith(b"\x89PNG\r\n\x1a\n")
    elif suffix in {".jpg", ".jpeg"}:
        valid = data.startswith(b"\xff\xd8\xff")
    else:
        try:
            decoded = data.decode("utf-8-sig")
            valid = not re.search(r"[\x00-\x08\x0b\x0e-\x1f]", decoded)
        except UnicodeError:
            valid = False
    if not valid:
        raise CapabilityError("The file contents do not match a supported document type.")
