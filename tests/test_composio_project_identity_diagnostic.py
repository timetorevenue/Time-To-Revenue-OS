"""Only the new root diagnostic: readonly and secret-safe at every failing stage."""
import json
import pytest
import composio_provider_acceptance as probe
import ttros_connections

KEY = 'ak_never_output_this_secret_123456'
PROJECT = 'pr_fixture_project'

@pytest.mark.parametrize('failure,stage', [('reader','credential_reader'), ('api','project_api_acceptance'), ('identity','project_identity')])
def test_new_diagnostic_reports_stage_without_raw_secret(monkeypatch, capsys, tmp_path, failure, stage):
    monkeypatch.setattr(probe, "STORE", tmp_path)
    def reader():
        if failure == 'reader': raise RuntimeError(KEY)
        return KEY
    calls = []
    def api(method, path, key, **kwargs):
        calls.append((method, path))
        assert key == KEY
        if (failure == 'api' or path == '/auth/session/info'):
            raise RuntimeError(KEY)
        return {'items': []}
    monkeypatch.setattr(probe, 'test_key', reader)
    monkeypatch.setattr(ttros_connections, 'composio_http', api)
    assert probe.verify_project_identity(PROJECT) == 1
    output = capsys.readouterr().out
    assert KEY not in output
    assert json.loads(output)['stage'] == stage
    assert all(method == 'GET' for method, _ in calls)

@pytest.mark.parametrize('project,result', [(PROJECT,'PASS'), ('pr_other_project','MISMATCH'), ('uuid-not-nanoid','UNVERIFIED')])
def test_new_diagnostic_verifies_exact_returned_project_only(monkeypatch, capsys, tmp_path, project, result):
    monkeypatch.setattr(probe, "STORE", tmp_path)
    monkeypatch.setattr(probe, 'test_key', lambda: KEY)
    def api(method, path, key, **kwargs):
        assert method == 'GET'
        return {'items': []} if path == '/connected_accounts' else {'project': {'nanoId': project, 'api_key': KEY}, 'secret': KEY}
    monkeypatch.setattr(ttros_connections, 'composio_http', api)
    probe.verify_project_identity(PROJECT)
    output = capsys.readouterr().out
    assert KEY not in output and json.loads(output)['result'] == result
