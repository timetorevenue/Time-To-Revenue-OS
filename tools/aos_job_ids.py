"""Permanent job identifiers, decimal sequence arithmetic and filesystem keys.

New jobs use AOS-YYYY-NNNN (minimum four digits, no maximum). Old J identifiers
remain valid. IDs are strings in JSON and APIs; never parse them as JS numbers.
Long identifiers use a digest as their filename, retaining the full ID in records.
"""
from __future__ import annotations

import hashlib
import re

AOS_PATTERN = r"AOS-[0-9]{4}-[0-9]{4,}"
LEGACY_JOB_PATTERN = r"J-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}"
AOS_ID_RE = re.compile(AOS_PATTERN + r"\Z")
JOB_ID_RE = re.compile(r"(?:" + AOS_PATTERN + "|" + LEGACY_JOB_PATTERN + r")\Z")
ID_IN_TEXT_RE = re.compile(r"(?<![A-Za-z0-9_])" + AOS_PATTERN + r"(?![A-Za-z0-9_])")


def decimal(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+", value):
        raise ValueError("sequence must be an ASCII decimal string")
    return value.lstrip("0") or "0"


def highest(left: str, right: str) -> str:
    left, right = decimal(left), decimal(right)
    return max((left, right), key=lambda n: (len(n), n))


def increment(value: str) -> str:
    # Avoid Python's int-to-decimal digit ceiling; sequence width has no application limit.
    digits = list(decimal(value))
    for index in range(len(digits) - 1, -1, -1):
        if digits[index] != "9":
            digits[index] = chr(ord(digits[index]) + 1)
            return "".join(digits)
        digits[index] = "0"
    return "1" + "".join(digits)


def storage_key(value: str) -> str:
    """Preserve old paths; hash long safe identifiers instead of capping their digits."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", value):
        raise ValueError("unsafe identifier")
    return value if len(value) <= 160 else "id-" + hashlib.sha256(value.encode("ascii")).hexdigest()
