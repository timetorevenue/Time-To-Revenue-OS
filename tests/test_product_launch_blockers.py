from unittest.mock import Mock

import pytest

import aos_jobs
import aos_product_launch as launch
from aos_authority import action_authorization
from aos_david import David


REQUEST = 'Create a TEST ONLY workflow audit offer'
NEGATIVE = [
    'Do not create a TEST ONLY workflow audit. Explain the safeguards only.',
    "Please don't launch a TEST ONLY workflow audit.",
    'Never build a TEST ONLY workflow audit offer.',
    'Create a TEST ONLY workflow audit? Explain the safeguards only.',
    'Explain the TEST ONLY workflow audit without creating any jobs.',
    'Can you describe a TEST ONLY workflow audit?',
    'Create a TEST ONLY workflow audit; do not submit the launch.',
    'Create a TEST ONLY workflow audit is not authorized.',
    'Create a TEST ONLY workflow audit? ',
    'Create a TEST ONLY workflow audit; only explain, no jobs.',
    'Create a TEST ONLY workflow audit, but it must not be launched.',
    'Create a TEST ONLY workflow audit; avoid creating any jobs.',
    '"Create a TEST ONLY workflow audit" is an example, not a command.',
]


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv('TTROS_STUDIO_STATE', str(tmp_path / 'studio'))
    provider = Mock(side_effect=AssertionError('Provider must not even be constructed'))
    monkeypatch.setattr(launch, 'StripeTest', provider)
    return tmp_path, provider


def test_david_jobs_no_connector_writes_blocks_before_checkout(isolated):
    root, provider = isolated
    david = David(root, model=Mock(side_effect=AssertionError('No model')), warm=False)
    try:
        reply = david.turn('restricted-launch', REQUEST + '; do not publish or send anything.')
    finally:
        david.close()
    job = aos_jobs.load(root, reply['job_id'])
    assert job['status'] == 'done'
    assert job['restrictions'] == ['no_brain_writes', 'no_connector_writes']
    assert launch.read(root)['stripe']['authorization_basis'] == 'task_restriction'
    assert len(aos_jobs.list_jobs(root)) == 1
    with pytest.raises(ValueError, match='no connector or provider changes'):
        launch.reconcile_checkout(root)
    provider.assert_not_called()


def test_jobs_authorized_test_connector_executes(isolated, monkeypatch):
    root, _ = isolated
    checkout = Mock(return_value={'provider_verified': True, 'simulation': True})
    provider = Mock(return_value=Mock(checkout=checkout))
    monkeypatch.setattr(launch, 'StripeTest', provider)
    job, _ = aos_jobs.create(root, conversation_id='positive', topic='product-launch', request=REQUEST,
                            executor={'kind': 'product_launch', 'action': 'draft', 'site': launch.SITE})
    assert aos_jobs.execute(root, job['id'])['status'] == 'done'
    provider.assert_called_once_with()
    checkout.assert_called_once()
    assert launch.read(root)['stripe']['provider_verified']


@pytest.mark.parametrize('message', NEGATIVE)
def test_negative_instruction_has_no_launch_side_effects(isolated, monkeypatch, message):
    root, provider = isolated
    david = David(root, model=Mock(side_effect=AssertionError('No model')), warm=False)
    create = Mock(side_effect=AssertionError('No job may be allocated'))
    monkeypatch.setattr(aos_jobs, 'create', create)
    local_write = Mock(side_effect=AssertionError('No launch write'))
    monkeypatch.setattr(launch, 'write', local_write)
    monkeypatch.setattr(launch.aos_crm, 'record_event', local_write)
    try:
        reply = david.turn('negative-launch', message)
        assert 'job_id' not in reply
        with pytest.raises(ValueError):
            launch.submit(root, message)
    finally:
        david.close()
    assert not (root / 'results').exists()
    assert not (root / 'studio').exists()
    assert not (root / 'queue/jobs').exists()
    create.assert_not_called()
    provider.assert_not_called()
    local_write.assert_not_called()


@pytest.mark.parametrize('message', NEGATIVE)
def test_jobs_cannot_bypass_negative_intent(isolated, message):
    root, provider = isolated
    job, _ = aos_jobs.create(root, conversation_id='bypass', topic='product-launch', request=message,
                            executor={'kind': 'product_launch', 'action': 'draft', 'site': launch.SITE})
    job = aos_jobs.execute(root, job['id'])
    assert job['status'] == 'refused' and job['failure_class'] == 'task_constraint'
    assert launch.read(root) is None
    assert not (root / 'studio').exists()
    assert not (root / 'queue/crm').exists()
    provider.assert_not_called()


@pytest.mark.parametrize('action', ['GMAIL_SEND_EMAIL', 'STRIPE_CHARGE', 'PUBLIC_PUBLISH', 'GOOGLEDRIVE_SHARE_FILE'])
def test_test_launch_does_not_authorize_protected_actions(tmp_path, action):
    assert not action_authorization(tmp_path, {'request': REQUEST}, action=action, target=launch.SITE)['authorized']


def test_http_negative_instruction_does_not_allocate_job(isolated, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from product_launch import router
    root, provider = isolated
    create = Mock(side_effect=AssertionError('No job allocation'))
    monkeypatch.setattr(aos_jobs, 'create', create)
    app = FastAPI()
    app.include_router(router(lambda: root))
    response = TestClient(app).post('/api/website/studio/launch', json={'request': NEGATIVE[0]})
    assert response.status_code == 400
    create.assert_not_called()
    provider.assert_not_called()
    assert not list(root.iterdir())
