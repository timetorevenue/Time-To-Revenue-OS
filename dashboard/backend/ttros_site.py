"""Runtime site configuration: which site and domain this installation serves.

Defaults are this installation's registered values; set the environment variables to serve another site.
    TTROS_STUDIO_SITE_ID   Studio site id (key in context/website_studio_sites.json)
    TTROS_SITE_DOMAIN      bare domain of the managed website, e.g. example.com
Revisit: when another per-installation constant is found hard-coded. · Last touched: 2026-11-26.
"""
from __future__ import annotations

import os
import re

_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
_DOMAIN = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$")


def _get(name: str, default: str, rx: re.Pattern) -> str:
    value = (os.environ.get(name) or default).strip().lower()
    if not rx.match(value):
        raise ValueError(f"{name} is not a valid value: {value!r}")
    return value


SITE_ID = _get("TTROS_STUDIO_SITE_ID", "default-site", _ID)
DOMAIN = _get("TTROS_SITE_DOMAIN", "core-site.example", _DOMAIN)
SITE_URL = f"https://www.{DOMAIN}"
