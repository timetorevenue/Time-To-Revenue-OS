"""Credential handoff never uses argv, plaintext files, or error output."""
from types import SimpleNamespace
import pytest
from composio_provider_acceptance import encrypt_key, REF


def test_hidden_key_is_only_sent_to_os_encrypt_stdin(tmp_path):
    key = 'ak_isolated_test_key_0123456789'
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout=b'encrypted-ciphertext')
    target = tmp_path / 'test.cred'
    encrypt_key(key, target, run=run)
    assert key not in str(calls[0][0])
    assert calls[0][1]['input'] == key.encode()
    assert calls[0][1]['capture_output']
    assert '--name=' + REF in calls[0][0]
    assert target.read_bytes() == b'encrypted-ciphertext'
    assert target.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        encrypt_key(key, target, run=run)
    assert target.read_bytes() == b'encrypted-ciphertext'


def test_encrypt_failure_never_writes_secret_or_echoes_provider_error(tmp_path):
    key = 'ak_isolated_test_key_0123456789'
    target = tmp_path / 'test.cred'
    def run(*args, **kwargs):
        return SimpleNamespace(returncode=1, stdout=b'', stderr=key.encode())
    with pytest.raises(ValueError) as error:
        encrypt_key(key, target, run=run)
    assert key not in str(error.value)
    assert not target.exists()


def test_provider_evidence_drops_nested_credentials_and_raw_state():
    from composio_provider_acceptance import public_metadata
    safe = public_metadata({'id': 'ca_test', 'user_id': 'test-user', 'state': {'token': 'never-output'},
                            'data': {'api_key': 'never-output'}, 'toolkit': {'slug': 'github', 'secret': 'never-output'},
                            'auth_config': {'id': 'ac_test', 'credentials': {'secret': 'never-output'}}})
    assert 'never-output' not in str(safe)
    assert safe['toolkit'] == {'slug': 'github'}
    assert safe['auth_config']['id'] == 'ac_test'
