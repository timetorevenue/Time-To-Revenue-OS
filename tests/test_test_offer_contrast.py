import copy

import pytest

import website_studio_contrast as contrast
import website_studio_edits as edits


@pytest.mark.parametrize('state', ['default', 'hover', 'focus-visible'])
def test_all_26_offer_fields_are_checked(state):
    data = edits.initial_data('test-offer')
    expected = {f"{item['props']['id']}:{field}" for item in data['content'] for field in item['props'] if field.startswith('text')}
    checks = contrast.checks(data, state)
    assert len(expected) == len(checks) == 34  # 215679e ServicePage: intro +3 texts, A practical first step (5)
    assert {check['id'] for check in checks} == expected
    assert all(check['pass'] for check in checks)


# The primary site button (paper text on the Primary fill) keeps its own colours inside a white section.
FIXED_BUTTONS = {'TestOffer0:text4', 'TestOffer5:text4'}


@pytest.mark.parametrize('section', range(6))
def test_offer_violations_and_compliant_overrides(section):
    data = edits.initial_data('test-offer')
    data['content'][section]['props']['styles'] = {'section': {'text': '#ffffff', 'background': '#ffffff'}}
    checks = [check for check in contrast.checks(data) if check['section'] == data['content'][section]['props']['id']]
    assert checks and all(check['pass'] == (check['id'] in FIXED_BUTTONS) for check in checks)
    data['content'][section]['props']['styles']['section']['text'] = '#111315'
    item = data['content'][section]
    for name, field in contrast.MODEL['sections'][item['type']]['fields'].items():
        if f"{item['props']['id']}:{name}" not in FIXED_BUTTONS:  # dark text on the dark primary button would rightly fail
            item['props']['styles'][field['target']] = {'text': '#111315'}
    assert not contrast.failures(data)


def test_missing_model_or_field_fails_closed(monkeypatch):
    model = copy.deepcopy(contrast.MODEL)
    monkeypatch.setattr(contrast, 'MODEL', model)
    del model['sections']['TestOffer0']['fields']['text0']
    with pytest.raises(ValueError, match='Missing contrast field'):
        contrast.checks(edits.initial_data('test-offer'))
    del model['sections']['TestOffer0']
    with pytest.raises(ValueError, match='Missing contrast model'):
        contrast.checks(edits.initial_data('test-offer'))


def test_offer_hover_uses_actual_contact_button_background():
    checks = {check['id']: check for check in contrast.checks(edits.initial_data('test-offer'), 'hover')}
    assert checks['TestOffer4:text2']['background'] == '#f0deba'
