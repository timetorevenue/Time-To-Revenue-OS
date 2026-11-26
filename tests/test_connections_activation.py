"""Activation boundaries against the complete candidate backend; no lifespan/credentials."""
from fastapi.testclient import TestClient
from starlette.requests import Request
import pytest
import main
from dashboard_identity import Identity
from ttros_connections import Connections, ConnectionError


def test_reconciled_main_preserves_connections_current_website_and_subscribers():
    paths = {r.path for r in main.app.routes if hasattr(r, 'path')}
    assert '/api/auth/session' in paths
    assert '/api/account-connections/hub/state' in paths
    assert '/api/account-connections/hub/connections/{ident}/jobs' in paths
    assert any(p.startswith('/api/current-website') for p in paths)
    assert '/api/subscribers/request' in paths


def test_production_identity_does_not_allow_loopback_preview(tmp_path):
    identity = Identity(tmp_path)
    request = Request({'type':'http','method':'GET','path':'/api/auth/session','headers':[],
                       'client':('127.0.0.1',1234),'server':('127.0.0.1',8010),'scheme':'http','query_string':b''})
    # Refuse before calling the production denial logger; it is rooted outside the fixture.
    from unittest.mock import patch
    with patch('remote_pc._record_denial'):
        with pytest.raises(Exception) as error:
            identity.principal(request)
        assert error.value.status_code == 403


def test_exact_hosted_origin_and_cross_site_boundary(tmp_path, monkeypatch):
    monkeypatch.setenv('TTROS_DASHBOARD_ORIGIN','https://ttros.core-site.example')
    identity = Identity(tmp_path)
    def req(origin, site='same-origin'):
        return Request({'type':'http','headers':[(b'x-ttros-connections',b'1'),(b'origin',origin.encode()),(b'sec-fetch-site',site.encode())]})
    identity.csrf(req('https://ttros.core-site.example'))
    for origin,site in [('https://evil.invalid','same-origin'),('http://localhost:4390','same-origin'),('https://ttros.core-site.example','cross-site')]:
        with pytest.raises(Exception) as error: identity.csrf(req(origin,site))
        assert error.value.status_code == 403


def test_connections_commands_require_trusted_ingress_before_david():
    from unittest.mock import patch
    client=TestClient(main.app)
    with patch('remote_pc._record_denial'):
        response=client.post('/api/dashboard/ask-david',json={'text':'/connections','conversation_id':'activation-fixture'},headers={'Origin':'https://evil.invalid','X-TTROS-Connections':'1'})
    assert response.status_code == 403
    assert main.DAVID is None and main.RUNNER is None
