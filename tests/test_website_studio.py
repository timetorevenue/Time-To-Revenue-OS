"""Offline proposal boundaries: no model, job runner or website mutation."""
import copy
import json
import sys
from pathlib import Path
from unittest.mock import Mock
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'dashboard/backend'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import website_studio as studio

@pytest.fixture
def data():
    s = json.loads(studio.SNAPSHOT.read_text())
    return {'root': {'props': {}}, 'content': [{'type': x['type'], 'props': {'id': x['type'], **x['texts']}} for x in s['sections']]}


def client(result):
    david = Mock()
    david._call.return_value = result
    app = FastAPI()
    app.include_router(studio.router(lambda: david))
    return TestClient(app), david


def test_proposal_reuses_david_without_execution(data):
    proposal = {'summary': 'Clearer headline', 'edits': [{'section': 'Section0', 'field': 'text0', 'text': 'Grow with clarity. '}]}
    c, david = client({'ok': True, 'json': {'action': 'reply', 'reply': json.dumps(proposal)}, 'usage': {'total_tokens': 42}})
    before = copy.deepcopy(data)
    r = c.post('/api/website/studio/propose', json={'project': 'default-site', 'request': 'Improve the headline', 'data': data})
    assert r.status_code == 200, r.text
    assert r.json()['usage']['total_tokens'] == 42
    assert data == before
    david._call.assert_called_once()
    david.turn.assert_not_called()
    david._delegate.assert_not_called()


@pytest.mark.parametrize('reply', [
    {'action': 'delegate', 'reply': '{}'},
    {'action': 'reply', 'reply': '{invalid'},
    {'action': 'reply', 'reply': json.dumps({'summary': 'bad', 'edits': [{'section': 'Section0', 'field': 'html', 'text': '<script/>'}]})},
    {'action': 'reply', 'reply': json.dumps({'summary': 'bad', 'edits': [], 'order': ['Section0']})},
])
def test_bad_model_output_fails_closed(data, reply):
    c, david = client({'ok': True, 'json': reply})
    assert c.post('/api/website/studio/propose', json={'project': 'default-site', 'request': 'change', 'data': data}).status_code == 422
    david._delegate.assert_not_called()


def test_scope_and_data_validation_precede_model(data):
    c, david = client({})
    for project, page in [('other-client', data), ('default-site', {'content': []})]:
        assert c.post('/api/website/studio/propose', json={'project': project, 'request': 'change', 'data': page}).status_code == 422
    david._call.assert_not_called()


def test_model_failure_is_visible(data):
    c, _ = client({'ok': False})
    assert c.post('/api/website/studio/propose', json={'project': 'default-site', 'request': 'change', 'data': data}).status_code == 503


def test_design_proposal_and_local_reset_are_bounded(data):
    proposal = {'summary': 'Brand update', 'edits': [], 'design': {'accent': '#0969a5', 'h1Weight': 700},
                'styles': [{'section': 'Section0', 'target': 'a-0', 'values': {'radius': 32}},
                           {'section': 'Section0', 'target': 'section', 'values': {'background': None}},
                           {'section': 'Section1', 'target': 'h2-0', 'values': None}]}
    c, david = client({'ok': True, 'json': {'action': 'reply', 'reply': json.dumps(proposal)}})
    response = c.post('/api/website/studio/propose', json={'project': 'default-site', 'request': 'Update our branding', 'data': data})
    assert response.status_code == 200, response.text
    assert response.json()['proposal'] == proposal
    assert 'design_schema' in david._call.call_args.args[0]


@pytest.mark.parametrize('changes', [
    {'design': {'accent': 'red;display:none'}}, {'design': {'h1Weight': True}},
    {'design': {'bodySize': 1000}}, {'design': {'h1Font': 'url(https://bad)'}},
    {'styles': [{'section': 'Section0', 'target': 'a-999', 'values': {'radius': 4}}]},
    {'styles': [{'section': 'Section0', 'target': 'section', 'values': {'buttonVariant': 'primary'}}]},
    {'styles': [{'section': 'Section0', 'target': 'a-0', 'values': {'unknown': None}}]},
    {'styles': [{'section': 'other-client', 'target': 'section', 'values': None}]},
])
def test_unsafe_design_proposal_fails_closed(data, changes):
    proposal = {'summary': 'Invalid', 'edits': [], **changes}
    with pytest.raises(ValueError):
        studio.validate_proposal(proposal, data)


@pytest.mark.parametrize('damage', [
    lambda data: data['root']['props'].update(design={'accent': '#000000'}),
    lambda data: data['content'][0]['props'].update(styles={'a-0': {'radius': -1}}),
    lambda data: data['content'][0]['props'].update(styles={'../path': {}}),
])
def test_invalid_draft_design_is_rejected_before_call(data, damage):
    c, david = client({})
    damage(data)
    response = c.post('/api/website/studio/propose', json={'project': 'default-site', 'request': 'change', 'data': data})
    assert response.status_code == 422
    david._call.assert_not_called()


def test_selected_element_is_validated_and_supplied_to_david(data):
    proposal = {'summary': 'Selected button', 'edits': [], 'styles': [{'section': 'Section0', 'target': 'a-0', 'values': {'radius': 32}}]}
    c, david = client({'ok': True, 'json': {'action': 'reply', 'reply': json.dumps(proposal)}})
    body = {'project': 'default-site', 'request': 'Round this button', 'data': data,
            'selection': {'section': 'Section0', 'target': 'a-0'}}
    assert c.post('/api/website/studio/propose', json=body).status_code == 200
    assert '"selection": {"section": "Section0", "target": "a-0"}' in david._call.call_args.args[0]
    david.reset_mock()
    body['selection']['section'] = 'other-client'
    assert c.post('/api/website/studio/propose', json=body).status_code == 422
    david._call.assert_not_called()
