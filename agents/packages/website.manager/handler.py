"""website.manager: one owner request about a Studio page becomes Studio editing operations.

Code refuses what the Website Manager never does (unsafe links, scripts or markup, sending,
publishing, deploying, other websites) before any model call, and asks for a selection when the
request is about an element and none is selected. The model reads the request against the page
context and returns operations; code then checks every operation: known ops only, element ids from
the context only, breakpoints d/t/m, style properties from the context's list, plain values, no
url() or script in CSS. The Studio validates again against the real documents and shows the
result as a proposal; nothing is applied here.

Revisit: when the Studio operation vocabulary or the context shape changes. · Last touched: 2026-11-22.
"""

import json
import re

UNSAFE = re.compile(r"(?i)\b(?:javascript|vbscript|data)\s*:|<\s*/?\s*(?:script|iframe|object|embed|style)\b|\bon[a-z]+\s*=")
OUT_OF_SCOPE = re.compile(r"(?i)\b(?:publish|deploy)\b|\bpush\b.{0,40}\b(?:live|production|vercel|github|origin)\b"
                          r"|\b(?:e-?mail|send|upload)\b.{0,40}\b(?:files?|source code|repository|repo|credentials?|passwords?|tokens?)\b"
                          r"|\b(?:delete|drop|wipe)\b.{0,40}\b(?:site|website|pages|repository|repo)\b")
ELEMENT_WORDS = re.compile(r"(?i)\b(?:this|it|button|link|heading|headline|text|image|section|card|column|font|size|colou?r|bigger|smaller|bold|spacing|padding|margin)\b")
SITE_WORDS = re.compile(r"(?i)\b(?:header|nav|navigation|css|stylesheet)\b")
CSS_BAD = re.compile(r"(?i)@import|url\s*\(|expression\s*\(|javascript:|behavior\s*:|<|\\")
VALUE_BAD = re.compile(r"[;{}<>\\]|url\s*\(|expression\s*\(|javascript:", re.I)
HEADER_MODES = ("sticky-transparent", "sticky-solid", "auto-hide", "static")
ATTRS = ("alt", "title", "aria-label")


def _escape(text):
    return str(text).replace("<", "&lt;").replace(">", "&gt;")


def _known_ids(context):
    ids = set()
    selected = context.get("selected") or {}
    for key in ("id", "link_id", "text_id", "style_target"):
        if selected.get(key):
            ids.add(str(selected[key]))
    for row in selected.get("ancestors") or []:
        if isinstance(row, dict) and row.get("id"):
            ids.add(str(row["id"]))
    for row in context.get("elements") or []:
        if isinstance(row, dict) and row.get("id"):
            ids.add(str(row["id"]))
    return ids


def _types(context):
    out = {}
    selected = context.get("selected") or {}
    for row in [selected] + list(selected.get("ancestors") or []) + list(context.get("elements") or []):
        if isinstance(row, dict) and row.get("id"):
            out[str(row["id"])] = (str(row.get("type") or "El"), str(row.get("tag") or ""))
    if selected.get("link_id"):
        out.setdefault(str(selected["link_id"]), ("El", "a"))
    if selected.get("text_id"):
        out.setdefault(str(selected["text_id"]), ("Txt", ""))
    return out


def _convert(row, context, ids, types, problems):
    kind = row.get("op")
    ident = row.get("id")
    if kind in ("set_style", "set_link", "set_text", "set_attr"):
        if not ident or ident not in ids:
            problems.append(f"{kind} names an element that is not on the page: {str(ident)[:40]!r}")
            return None
    if kind == "set_style":
        bp = row.get("bp") or context.get("breakpoint") or "d"
        allowed = set(context.get("style_props") or [])
        values = {}
        for pair in row.get("styles") or []:
            prop, value = str(pair.get("prop") or ""), pair.get("value")
            if allowed and prop not in allowed:
                problems.append(f"unknown style property {prop[:30]!r}")
                continue
            if value is not None and (VALUE_BAD.search(str(value)) or len(str(value)) > 240):
                problems.append(f"unsupported value for {prop}")
                continue
            values[prop] = value
        if types.get(ident, ("El", ""))[0] == "Txt":
            problems.append("a text run cannot be styled; style its element")
            return None
        if bp not in ("d", "t", "m") or not values:
            problems.append("set_style needs a breakpoint and at least one property")
            return None
        return {"op": "set_style", "id": ident, "bp": bp, "values": values}
    if kind == "set_link":
        href = str(row.get("href") or "").strip()
        if not href or UNSAFE.search(href):
            problems.append("set_link needs a safe destination")
            return None
        op = {"op": "set_link", "id": ident, "href": href}
        if row.get("new_tab") is not None:
            op["newTab"] = bool(row["new_tab"])
        return op
    if kind == "set_text":
        text = str(row.get("text") or "")
        if not text.strip() or UNSAFE.search(text):
            problems.append("set_text needs plain text")
            return None
        if types.get(ident, ("", ""))[0] != "Txt":
            problems.append("set_text must name a text run")
            return None
        return {"op": "set_text", "id": ident, "text": text}
    if kind == "set_attr":
        if row.get("attr") not in ATTRS or UNSAFE.search(str(row.get("value") or "")):
            problems.append("set_attr may only set alt, title or aria-label text")
            return None
        return {"op": "set_attr", "id": ident, "name": row["attr"], "value": row.get("value")}
    if kind == "set_css":
        css = str(row.get("css") or "")
        if CSS_BAD.search(css) or css.count("{") != css.count("}"):
            problems.append("CSS must be plain rules without imports, url() or markup")
            return None
        return {"op": "set_css", "css": css}
    if kind == "set_header":
        header = row.get("header") or {}
        if any(header.get(bp) not in HEADER_MODES for bp in ("desktop", "tablet", "mobile")):
            problems.append("header modes must be sticky-transparent, sticky-solid, auto-hide or static")
            return None
        threshold = header.get("threshold")
        threshold = threshold if isinstance(threshold, int) and 0 <= threshold <= 400 else 8
        return {"op": "set_header", "header": {"desktop": header["desktop"], "tablet": header["tablet"], "mobile": header["mobile"],
                                               "threshold": threshold}}
    problems.append(f"unknown operation {str(kind)[:30]!r}")
    return None


OTHER_SITE = re.compile(r"(?i)\b(?:another|other|different)\s+(?:site|website|client)\b|\b(?:on|for|of)\s+((?:[a-z0-9-]+\.)+(?:com|ca|net|org|io|co))\b")


def run(input, ctx):
    request = input["request"].strip()
    context = input.get("context") or {}
    if not isinstance(context.get("page"), dict) or not context.get("site"):
        return {"status": "failed", "failure_class": "context_unavailable", "detail": "the Studio sent no page context"}
    other = OTHER_SITE.search(request)
    if other and (not other.group(1) or other.group(1).lower() not in {str(context.get("domain") or "").lower(), "www." + str(context.get("domain") or "").lower()}):
        return {"status": "failed", "failure_class": "scope_denied", "detail": "the Website Manager edits only the website open in the Studio"}
    if UNSAFE.search(request):
        return {"status": "refused", "failure_class": "unsafe_request",
                "detail": "the Website Manager does not add scripts, markup or script/data links"}
    if OUT_OF_SCOPE.search(request):
        return {"status": "refused", "failure_class": "out_of_scope",
                "detail": "the Website Manager proposes edits in the Studio only; sending, publishing and deploying stay with the owner"}
    if not (context.get("selected") or {}).get("id") and ELEMENT_WORDS.search(request) and not SITE_WORDS.search(request):
        return {"status": "needs_input", "detail": "no element is selected",
                "output": {"reply": "Select the element you mean on the canvas, then ask again.", "ops": [], "needs_input": True,
                           "missing": ["the element to change (select it on the canvas)"]}}

    compact = {key: context.get(key) for key in ("site", "page", "breakpoint", "selected", "elements", "brand", "code", "routes", "style_props")
               if context.get(key) is not None}
    prompt = ctx.prompt.replace("{{request}}", _escape(request)).replace("{{context}}", _escape(json.dumps(compact, ensure_ascii=False)))
    draft = ctx.model(prompt, schema=ctx.schemas["model_output.json"])
    reply = str(draft.get("reply") or "").strip()[:600]
    if draft.get("needs_input") or not draft.get("ops"):
        missing = [str(item)[:200] for item in draft.get("missing") or []][:4] or ["a more specific request"]
        return {"status": "needs_input", "detail": "the request needs more detail",
                "output": {"reply": reply or "I need a little more detail.", "ops": [], "needs_input": True, "missing": missing}}

    ids, types = _known_ids(context), _types(context)
    problems, ops = [], []
    for row in draft["ops"][:40]:
        op = _convert(row, context, ids, types, problems)
        if op:
            ops.append(op)
    if problems or not ops:
        return {"status": "failed", "failure_class": "validation_failed", "detail": "; ".join(problems[:4]) or "no usable operation"}
    return {"status": "ok", "output": {"reply": reply or "Proposed changes are ready to preview.", "ops": ops, "needs_input": False, "missing": []}}
