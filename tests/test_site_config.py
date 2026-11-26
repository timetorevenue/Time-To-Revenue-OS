"""Runtime site identity: configured by environment, validated, defaults unchanged."""
import importlib
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1] / "dashboard" / "backend"
sys.path.insert(0, str(BACKEND))


def load(monkeypatch, **env):
    for k in ("TTROS_STUDIO_SITE_ID", "TTROS_SITE_DOMAIN"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import ttros_site
    return importlib.reload(ttros_site)


def test_defaults_are_the_registered_values(monkeypatch):
    m = load(monkeypatch)
    assert (m.SITE_ID, m.DOMAIN, m.SITE_URL) == ("default-site", "core-site.example", "https://www.core-site.example")


def test_environment_configures_site_and_domain(monkeypatch):
    m = load(monkeypatch, TTROS_STUDIO_SITE_ID="acme-site", TTROS_SITE_DOMAIN="Acme.Example.org")
    assert (m.SITE_ID, m.DOMAIN, m.SITE_URL) == ("acme-site", "acme.example.org", "https://www.acme.example.org")


@pytest.mark.parametrize("name,value", [("TTROS_STUDIO_SITE_ID", "../x"), ("TTROS_STUDIO_SITE_ID", "A B"), ("TTROS_SITE_DOMAIN", "evil.com/path"), ("TTROS_SITE_DOMAIN", "nodot")])
def test_invalid_values_are_refused(monkeypatch, name, value):
    with pytest.raises(ValueError):
        load(monkeypatch, **{name: value})


def test_backend_modules_follow_the_configured_site(monkeypatch):
    load(monkeypatch, TTROS_STUDIO_SITE_ID="acme-site", TTROS_SITE_DOMAIN="acme.example.org")
    for mod in ("website_studio_seo", "website_studio_manager"):
        sys.modules.pop(mod, None)
    import website_studio_seo
    assert website_studio_seo.SITE == "https://www.acme.example.org"
    load(monkeypatch)
    sys.modules.pop("website_studio_seo", None)
