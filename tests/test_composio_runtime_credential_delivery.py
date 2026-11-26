"""New provisioner custody checks; native delivery is also verified live as backend user."""
import json,os
from pathlib import Path
from types import SimpleNamespace
import pytest
import composio_provider_acceptance as provisioner

KEY='ak_never_put_in_a_file_or_env_123456'
PROJECT='pr_sJPPh34mNls8'

@pytest.fixture
def runtime(tmp_path,monkeypatch):
    (tmp_path/'.config/systemd/user/aos-backend.service.d').mkdir(parents=True)
    monkeypatch.setattr(provisioner.os,'geteuid',lambda:0)
    monkeypatch.setattr(provisioner.pwd,'getpwnam',lambda _:SimpleNamespace(pw_dir=str(tmp_path),pw_uid=os.getuid(),pw_gid=os.getgid(),pw_name='casey'))
    monkeypatch.setattr(provisioner,'test_key',lambda:KEY)
    monkeypatch.setattr(provisioner,'verify_project_witness',lambda *a:{'result':'PASS'})
    calls=[]
    def run(argv,**kwargs):
        calls.append((argv,kwargs))
        assert KEY not in str(argv) and KEY not in str(kwargs.get('env'))
        if argv[-1]=='setup':
            p=Path(kwargs['env']['SYSTEMD_CREDENTIAL_SECRET']);p.write_bytes(b'native-host-format-placeholder');p.chmod(0o600)
            return SimpleNamespace(returncode=0,stdout=b'',stderr=b'')
        if 'encrypt' in argv:
            assert kwargs['input']==KEY.encode()
            return SimpleNamespace(returncode=0,stdout=b'encrypted-envelope',stderr=b'')
        return SimpleNamespace(returncode=0,stdout='{"result":"PASS"}',stderr='')
    monkeypatch.setattr(provisioner.subprocess,'run',run)
    return tmp_path,calls


def test_native_reenvelope_has_no_plaintext_persistence_and_stages_only(runtime,capsys):
    root,calls=runtime
    provisioner.provision_runtime_project(PROJECT)
    result=json.loads(capsys.readouterr().out)
    assert result['backend_user_read_verified'] and not result['runtime_requires_sudo']
    assert result['original_credential_preserved'] and not result['service_restarted']
    for p in root.rglob('*'):
        if p.is_file():assert KEY.encode() not in p.read_bytes()
    pending=Path(result['service_configuration_staged'])
    assert pending.name.endswith('.pending') and pending.stat().st_mode & 0o777==0o600
    assert not pending.with_suffix('').exists()
    assert len([a for a,k in calls if 'encrypt' in a])==1


def test_existing_runtime_envelope_is_not_replaced(runtime,capsys):
    root,calls=runtime
    provisioner.provision_runtime_project(PROJECT);capsys.readouterr()
    from ttros_connections import MANAGED_REF
    target=root/'.config/ttros/credentials/composio'/(MANAGED_REF+'.cred')
    original=target.read_bytes()
    provisioner.provision_runtime_project(PROJECT)
    assert target.read_bytes()==original
    assert len([a for a,k in calls if 'encrypt' in a])==1
    assert not json.loads(capsys.readouterr().out)['same_provider_key_reenveloped']
