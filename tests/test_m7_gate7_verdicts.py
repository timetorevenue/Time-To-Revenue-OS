"""Gate 7 declared-verdict agreement and forced-failure evidence classes (offline, SYNTHETIC fixtures).

Repeats agree only when every declared decision field is exactly equal; different wording of the
same decision agrees, a different decision never does, and anything missing, invalid or ambiguous
fails closed. All jobs, outputs, receipts and ledger rows here are invented under a temp dir.
"""
import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / 'tools', ROOT / 'tests/fixtures/m7'):
    sys.path.insert(0, str(path))
import m7_fixture as fx  # noqa: E402
from aos_agents import release, reliability, verdicts  # noqa: E402
from aos_agents.package import load_package  # noqa: E402
from aos_agents.runtime import canonical_sha256  # noqa: E402

CLASSIFY, PLANNER, RETRIEVE, FILING = ('knowledge.source_classify', 'shared.planner',
                                       'shared.brain_retrieve', 'knowledge.filing_proposal')


@pytest.fixture
def root(tmp_path):
    return fx.make_root(tmp_path)[0]


def out(root, name, label='ok', run=0, **change):
    value = fx.synthetic_output(load_package(name, root), label, run)
    value.update(change)
    return value


def key(root, name, output):
    return verdicts.key(verdicts.extract(load_package(name, root), root, output))


# -- the verdict itself ------------------------------------------------------------------

@pytest.mark.parametrize('name', [CLASSIFY, PLANNER, RETRIEVE, FILING])
def test_same_decision_in_different_words_agrees(root, name):
    runs = [out(root, name, 'ok', run) for run in range(3)]
    assert len({canonical_sha256(r) for r in runs}) == 3          # the old whole-output hash disagreed
    assert len({key(root, name, r) for r in runs}) == 1


@pytest.mark.parametrize('name,change', [
    (CLASSIFY, {'kind': 'email'}), (CLASSIFY, {'sensitivity': 'confidential'}), (CLASSIFY, {'date': '2026-01-02'}),
    (CLASSIFY, {'scope_hint': 'global'}), (FILING, {'action': 'hold_for_review'}), (FILING, {'proposes_write': False}),
    (FILING, {'target': 'business_brain:sources/other.md'}), (RETRIEVE, {'refs': ['business_brain:HOME.md']}),
])
def test_a_different_substantive_decision_never_agrees(root, name, change):
    base = out(root, name)
    if name == RETRIEVE:
        change = {**change, 'sources': base['sources'][:1]}
    assert key(root, name, base) != key(root, name, {**base, **change})


def test_planner_task_or_record_change_is_a_different_verdict(root):
    base = out(root, PLANNER)
    other_task = copy.deepcopy(base)
    other_task['tasks'][0]['task'] = 'research_brief'
    other_record = copy.deepcopy(base)
    other_record['tasks'][0]['records'] = ['business_brain:sources/fixture/elsewhere.md']
    keys = {key(root, PLANNER, o) for o in (base, other_task, other_record)}
    assert len(keys) == 3


def test_order_matters_only_where_declared_unordered(root):
    retrieve = out(root, RETRIEVE)
    flipped = {**retrieve, 'refs': retrieve['refs'][::-1], 'sources': retrieve['sources'][::-1]}
    assert key(root, RETRIEVE, retrieve) == key(root, RETRIEVE, flipped)       # refs: a declared set
    planner = out(root, PLANNER)
    planner['tasks'][0]['records'] = ['business_brain:a.md', 'business_brain:b.md']
    swapped = copy.deepcopy(planner)
    swapped['tasks'][0]['records'] = swapped['tasks'][0]['records'][::-1]
    assert key(root, PLANNER, planner) != key(root, PLANNER, swapped)          # ordered unless declared


@pytest.mark.parametrize('broken', ['missing', 'invalid_enum', 'schema', 'multiline_label', 'not_object', 'bool_for_enum'])
def test_missing_invalid_or_ambiguous_verdicts_fail_closed(root, broken):
    value = out(root, CLASSIFY)
    if broken == 'missing':
        del value['kind']
    elif broken == 'invalid_enum':
        value['kind'] = 'memo'
    elif broken == 'schema':
        value['summary'] = 'short'
    elif broken == 'multiline_label':
        value['scope_hint'] = 'client:a\nclient:b'
    elif broken == 'not_object':
        value = ['kind', 'email']
    elif broken == 'bool_for_enum':
        value['kind'] = True
    with pytest.raises(verdicts.VerdictError):
        verdicts.extract(load_package(CLASSIFY, root), root, value)


@pytest.mark.parametrize('fields,problem', [
    ([{'pointer': '/summary', 'kind': 'label'}], 'free text'),
    ([{'pointer': '/summary', 'kind': 'enum'}], 'enum'),
    ([{'pointer': '/kind', 'kind': 'enum'}, {'pointer': '/kind', 'kind': 'enum'}], 'twice'),
    ([{'pointer': '/entities/*/name', 'kind': 'label', 'unordered': True}], 'uniqueItems'),
    ([{'pointer': '/nope', 'kind': 'enum'}], 'not in the schema'),
    ([{'pointer': '/kind', 'kind': 'guess'}], 'kind'),
    ([], 'no reliability verdict'),
])
def test_declarations_that_do_not_fit_the_schema_are_refused(root, fields, problem):
    path = root / verdicts.DECLARATIONS_PATH
    data = json.loads(path.read_text())
    data['agents'][CLASSIFY]['fields'] = fields
    path.write_text(json.dumps(data))
    with pytest.raises(verdicts.VerdictError, match=problem):
        verdicts.declaration(load_package(CLASSIFY, root), root)


def test_optional_fields_cannot_be_verdicts(root):
    schema = {'type': 'object', 'required': [], 'properties': {'a': {'enum': ['x', 'y']}}}
    assert any('optional' in p for p in verdicts.check({'fields': [{'pointer': '/a', 'kind': 'enum'}]}, schema))


def test_undeclared_agents_have_no_verdict(root):
    with pytest.raises(verdicts.VerdictError, match='no reliability verdict is declared'):
        verdicts.declaration(load_package('research.synthesise', root), root)


def test_declaring_verdicts_leaves_package_identity_unchanged(root):
    entry = fx.registry_entry(root, CLASSIFY)
    assert load_package(CLASSIFY, root).content_sha256 == entry['package_sha256']
    assert not any(part.startswith('reliability_verdicts') for part in
                   (p.name for p in (root / 'agents' / 'packages' / CLASSIFY).rglob('*')))


# -- gate 7 end to end -------------------------------------------------------------------

def gate(root, name=CLASSIFY):
    return release.gate7(load_package(name, root), root)


@pytest.mark.parametrize('name', [CLASSIFY, PLANNER, RETRIEVE, FILING])
def test_paraphrased_repeats_pass_gate7_and_keep_every_output_hash(root, name):
    path = fx.write_reliability(root, name)
    result = gate(root, name)
    assert result['pass'], result['problems']
    data = json.loads(path.read_text())
    assert result['same_verdict'] == 1.0
    assert all(len(set(row['output_sha256'])) == 3 for row in data['repeats'])     # full hashes preserved, all differ
    assert data['verdict_spec']['spec_sha256'] == verdicts.declaration(load_package(name, root), root)['spec_sha256']


def test_different_decisions_fail_the_85_percent_threshold(root):
    fx.write_reliability(root, CLASSIFY, repeats=[{'case': f'c{i}', 'verdicts': ['a', 'b', 'c']} for i in range(5)])
    result = gate(root)
    assert not result['pass'] and result['same_verdict'] == 0.333


def tamper(root, change):
    path = fx.write_reliability(root, CLASSIFY)
    data = json.loads(path.read_text())
    source = path.parent / data['provenance']['path']
    bundle = json.loads(source.read_text())
    change(bundle, data)
    source.write_text(json.dumps(bundle))
    data['provenance']['sha256'] = reliability.digest(bundle)
    path.write_text(json.dumps(data))
    return gate(root)


def _run(bundle):
    return bundle['cases'][0]['runs'][0]


@pytest.mark.parametrize('label,change', [
    ('output edited, hash kept', lambda b, d: _run(b)['output'].update(kind='email')),
    ('output dropped', lambda b, d: _run(b).pop('output')),
    ('output and job hash edited, receipt not', lambda b, d: (
        _run(b)['output'].update(kind='email'),
        _run(b)['job']['result']['steps'][0]['execution_identity'].update(output_sha256=canonical_sha256(_run(b)['output'])))),
    ('summary claims a verdict the outputs do not show', lambda b, d: d['repeats'][0].update(verdicts=['verdict:forged'] * 3)),
    ('version-1 provenance', lambda b, d: b.update(schema='ttros-reliability-1')),
    ('package ran twice in one job', lambda b, d: _run(b)['job']['result']['steps'].append(
        copy.deepcopy(_run(b)['job']['result']['steps'][0]))),
    ('repeat on a stand-in transport', lambda b, d: _run(b)['ledger'][0]['model_selection']['transport'].update(production_binary=False)),
    ('repeat without transport provenance', lambda b, d: _run(b)['ledger'][0]['model_selection'].pop('transport')),
    ('kill probe on a stand-in transport', lambda b, d: b['kill']['source']['ledger'][0]['model_selection']['transport'].update(production_binary=False)),
])
def test_gate7_provenance_tampering_fails_closed(root, label, change):
    result = tamper(root, change)
    assert not result['pass'], label
    assert any('reliability provenance invalid' in p for p in result['problems']), (label, result['problems'])


def test_editing_the_declaration_after_collection_invalidates_the_evidence(root):
    fx.write_reliability(root, CLASSIFY)
    path = root / verdicts.DECLARATIONS_PATH
    data = json.loads(path.read_text())
    data['agents'][CLASSIFY]['fields'] = data['agents'][CLASSIFY]['fields'][:1]       # weaken: compare kind only
    path.write_text(json.dumps(data))
    result = gate(root)
    assert not result['pass']
    assert any('differs from preserved run provenance' in p for p in result['problems'])


# -- forced-failure evidence classes ---------------------------------------------------------

def forced(root, **change):
    def apply(bundle, data):
        source = bundle['forced']['source']
        for k, v in change.items():
            if k == 'production_binary':
                source['ledger'][0]['model_selection']['transport']['production_binary'] = v
            elif k == 'attempts':
                source['call_attempts'] = v
            else:
                bundle['forced'][k] = v
        data['forced_failure'] = reliability.forced_failure_summary(bundle['forced'], load_package(CLASSIFY, root))
    return tamper(root, apply)


def test_genuine_provider_post_launch_forced_failure_qualifies(root):
    result = forced(root)
    assert result['pass'], result['problems']


def test_injected_transport_evidence_is_valid_but_does_not_qualify(root):
    result = forced(root, evidence_class='injected_transport', production_binary=False)
    assert not result['pass']
    assert result['problems'] == ["forced failure evidence class 'injected_transport' does not qualify (accepted: genuine_provider)"]


def test_a_stand_in_cannot_be_labelled_genuine(root):
    with pytest.raises(ValueError, match='contradicts its transport provenance'):
        forced(root, production_binary=False)


def test_a_pre_launch_stop_is_not_a_forced_post_launch_failure(root):
    started = {'agent': 'x', 'step': 's', 'input_sha256': 'i', 'package_sha256': 'p'}
    attempts = [{**started, 'result': {'status': 'started'}}, {**started, 'result': {'status': 'not_launched'}}]
    result = forced(root, attempts=attempts)
    assert not result['pass']
    assert 'forced failure did not stop after a launched model call' in result['problems']


def test_undeclared_evidence_class_is_refused(root):
    with pytest.raises(ValueError, match='no declared evidence class'):
        forced(root, evidence_class=None)
    with pytest.raises(ValueError, match='evidence_class must be one of'):
        reliability.capture_forced_failure(root, 'AOS-2026-10000', 'quota', evidence_class='whatever')
