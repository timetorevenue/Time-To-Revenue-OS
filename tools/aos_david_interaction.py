"""Lazy browser, desktop and visual evidence for David's existing turn loop."""
from __future__ import annotations

import io
import re
import shutil
import subprocess
import sys
from pathlib import Path

from aos_capabilities import Capability, CapabilityError
from aos_david_files import ARTIFACT_LIMIT, FileError, Files
from aos_david_general import _integer, _object, _string

ID = _string(32, pattern="^[a-f0-9]{32}$")
VISUAL = re.compile(r"\b(?:image|photo|picture|screenshot|chart|visual|design|look like|color|colour)\b", re.I)
INTERACT = re.compile(
    r"\b(?:browser|browse|navigate|website|webpage|web page|landing page|screenshot|download|"
    r"scroll|click|fill (?:in|out)|form|desktop|computer|authenticated session)\b", re.I)
ASSESS = re.compile(r"\b(?:assess|analy[sz]e|describe|compare|inspect|examine|evaluate|review|what|identify|interpret)\b", re.I)
IMAGE_FORMATS = {"PNG", "JPEG", "WEBP"}


def normalize_image(data):
    try:
        from PIL import Image, ImageOps, UnidentifiedImageError
    except ImportError:
        raise CapabilityError("Image processing requires the optional Pillow dependency.") from None
    try:
        with Image.open(io.BytesIO(data), formats=sorted(IMAGE_FORMATS)) as image:
            if image.width * image.height > 16_000_000:
                raise CapabilityError("Image dimensions exceed the visual input budget.")
            normalized = ImageOps.exif_transpose(image)
            normalized.thumbnail((1280, 960), Image.Resampling.LANCZOS)
            # Composite transparency and remove metadata before model submission.
            rgba = normalized.convert("RGBA")
            rgb = Image.new("RGB", rgba.size, "white")
            rgb.paste(rgba, mask=rgba.getchannel("A"))
            target = io.BytesIO()
            rgb.save(target, "JPEG", quality=82)
            return target.getvalue()
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        if isinstance(exc, CapabilityError):
            raise
        raise CapabilityError("Use a valid, bounded PNG, JPEG or WebP image.") from None


class Interaction:
    def __init__(self, root, scope, conversation, attachment_ids, text, authorize):
        self.files = Files(root, scope, attachment_ids, conversation_id=conversation, private_only=True)
        self.text = text
        self.authorize = authorize
        self.browser = None
        self.images = ()
        self.visual_calls = 0

    def close(self):
        if self.browser:
            self.browser.close()

    def attachments(self):
        rows = []
        for ident in list(self.files.attachments)[:10]:
            try:
                rows.append(self.files._metadata(ident, self.files.attachments))
            except (OSError, ValueError):
                continue
        return rows

    def browser_steps(self, args):
        if not INTERACT.search(self.text):
            raise CapabilityError("Browser actions need a browser task in the current operator request.")
        if re.search(r"\b(?:authenticated|signed-in|logged-in|existing browser) session\b", self.text, re.I):
            raise CapabilityError("There is no account-bound browser session available to David. Use a scoped connector.")
        if self.browser is None:
            from aos_david_browser import Browser
            self.browser = Browser(self.files, self.text, self.authorize)
        visual = [step for step in args["steps"] if step.get("visual")]
        if visual and (not (VISUAL.search(self.text) and ASSESS.search(self.text))
                       or len(visual) > 2 or any(step["op"] != "screenshot" for step in visual)):
            raise CapabilityError("Use visual screenshots only for requested visual analysis, up to two images.")
        result = self.browser.run(args)
        ids = [row["id"] for step, row in zip(args["steps"], result["steps"], strict=False)
               if step.get("visual") and row.get("id")]
        if ids:
            try:
                result["visual_input"] = self.inspect_images({"file_ids": list(dict.fromkeys(ids))})
            except CapabilityError as exc:
                result.update(status="partial", error=str(exc))
        return result

    def inspect_images(self, args):
        self.authorize()
        if not (VISUAL.search(self.text) or self.files.attachments) or self.visual_calls >= 2:
            raise CapabilityError("Visual analysis needs a relevant operator request and remaining visual budget.")
        rows, images = [], []
        for ident in args["file_ids"]:
            row, data = self.files.verified_bytes(ident)
            images.append(normalize_image(data))
            rows.append({key: row[key] for key in ("id", "filename", "sha256")})
        self.images = tuple(images)
        self.visual_calls += 1
        return {"status": "prepared_visual_input", "images": rows}

    def desktop(self, args):
        self.authorize()
        if not re.search(r"\b(?:desktop|computer)\b", self.text, re.I):
            raise CapabilityError("A desktop preview needs a desktop request in this turn.")
        row, data = self.files.verified_bytes(args["file_id"])
        suffix = Path(row["filename"]).suffix.lower()
        if suffix in {".jpg", ".jpeg", ".png", ".webp"}:
            data, mode = normalize_image(data), "image"
        elif suffix in {".txt", ".md", ".csv", ".json"}:
            try:
                data, mode = data[:50_000].decode("utf-8-sig").encode(), "text"
            except UnicodeError:
                raise CapabilityError("The text file is not valid UTF-8.") from None
        else:
            raise CapabilityError("Desktop preview supports scoped text and image files.")
        result = _desktop_capture(data, mode, args.get("scroll", 0))
        return {"operation": "desktop_preview",
                **self.files.store("desktop-preview.jpg", result, media=True)}

    def capabilities(self):
        from aos_david_browser import ROLES

        def cap(ident, desc, run, schema, required):
            def execute(args):
                try:
                    return run(args)
                except FileError as exc:
                    raise CapabilityError(str(exc)) from None
            return Capability(ident, desc, "verified operator conversation", "current operator request and resource scope",
                              lambda: (schema(), {"type": "object", "required": required}),
                              execute, auto_inspect=True)

        def browser_schema():
            return _object(("steps",), steps={"type": "array", "minItems": 1, "maxItems": 5, "items": _object(
                ("op",), op={"enum": ["navigate", "read", "click", "scroll", "fill", "upload", "download",
                                      "screenshot", "tabs", "open_tab", "close_tab"]},
                url=_string(1600), tab=_string(20, pattern="^[a-z0-9_-]+$"), name=_string(120),
                role={"enum": ROLES}, value=_string(800), file_id=ID, pixels=_integer(1200, -1200),
                visual={"type": "boolean", "description": "Attach screenshot pixels to the next model call for requested assessment."})})

        return [
            cap("browser.session", "Navigate public sites by URL or named link, read DOM/accessible labels, click local UI, "
                "scroll, prepare a form offline, manage tabs, save a screenshot or download a document. Batch up to five steps. "
                "Use name as the exact accessible label, or as the filename stem for screenshots.",
                self.browser_steps, browser_schema, ["status", "steps", "artifacts"]),
            cap("vision.inspect", "Attach up to two scoped screenshot or uploaded image IDs to the next subscription model call. "
                "Analyze their actual pixels or compare visual states.",
                self.inspect_images, lambda: _object(("file_ids",), file_ids={
                    "type": "array", "minItems": 1, "maxItems": 2, "uniqueItems": True, "items": ID}), ["images"]),
            cap("computer.preview", "Open a scoped image or text file in a private Linux graphical viewer, scroll and capture its display.",
                self.desktop, lambda: _object(("file_id",), file_id=ID, scroll=_integer(25, -25)), ["id", "sha256"]),
        ]


def _desktop_capture(data, mode, scroll):
    xvfb = shutil.which("xvfb-run")
    timeout = shutil.which("timeout")
    if not xvfb or not timeout or not shutil.which("xauth"):
        raise CapabilityError("The isolated Linux desktop viewer requires Xvfb and xauth.")
    try:
        result = subprocess.run([timeout, "--kill-after=1", "8", xvfb, "-a", "-s",
                                 "-screen 0 960x660x24 -nolisten tcp",
                                 sys.executable, "-I", str(Path(__file__).with_name("aos_david_desktop.py")), mode, str(scroll)],
                                input=data, capture_output=True, check=True, timeout=10,
                                env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"})
    except (OSError, subprocess.SubprocessError):
        raise CapabilityError("The isolated Linux graphical viewer could not complete.") from None
    if not result.stdout.startswith(b"\xff\xd8\xff") or len(result.stdout) > ARTIFACT_LIMIT:
        raise CapabilityError("The graphical viewer did not produce a valid bounded screenshot.")
    return result.stdout
