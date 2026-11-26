"""@@NAME@@: pure code, no model.

Revisit: when the task or its output contract changes.
"""


def run(input, ctx):
    items = sorted({str(item).strip() for item in input["items"]} - {""})
    if not items:
        return {"status": "needs_input", "detail": "no items were supplied", "output": {"missing": ["at least one non-empty item"]}}
    return {"status": "ok", "output": {"count": len(items), "items": items}}
