"""Repeats-only Gate 7 evidence for tool agents (zero model calls). Job sources are SYNTHETIC fixtures:
the real isolated run is proofs/m7_tool_repeats_2026-11-17/."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / 'tools', ROOT / 'tests/fixtures/m7'):
    sys.path.insert(0, str(path))
import m7_fixture as fx  # noqa: E402
from aos_agents import release, reliability  # noqa: E402
from aos_agents.package import load_package  # noqa: E402

TOOLS = ('shared.brain_retrieve', 'knowledge.filing_proposal')


@pytest.fixture
def roots(tmp_path):
    return fx.make_root(tmp_path)


def collect(root, runtime, name, out_dir, cases=5):
    ids = {str(i): [fx.add_job(root, runtime, i * 3 + j + 1, (name,), output_label=f'case-{i}')['id'] for j in range(3)]
           for i in range(cases)}
    return reliability.produce_repeats(name, ids, root, runtime, out_dir, runtime_class='isolated_nonproduction'), ids


@pytest.mark.parametrize('name', TOOLS)
def test_repeats_evidence_agrees_but_can_never_qualify(roots, name, tmp_path):
    root, runtime = roots
    package = load_package(name, root)
    path, _ = collect(root, runtime, name, tmp_path / 'out')
    data = reliability.verify_repeats(path, package, root)
    assert [len(set(r['verdicts'])) for r in data['repeats']] == [1] * 5
    assert all(len(set(r['output_sha256'])) == 3 for r in data['repeats'])      # wording differs, decision does not
    assert data['gate7_eligible'] is False and data['production_qualification'] is False
    assert 'forced post-launch quota/auth failure probe (genuine_provider)' in data['missing_for_gate7']
    # written outside the package under a name gate 7 never reads: the gate still has no evidence
    assert not (package.directory / 'evidence' / path.name).exists()
    gate = release.gate7(package, root)
    assert not gate['pass'] and any('no reliability evidence' in p for p in gate['problems'])


@pytest.mark.parametrize('label,mutate', [
    ('output', lambda b: b['cases'][0]['runs'][0]['output'].update(extra='x')),
    ('receipt', lambda b: b['cases'][0]['runs'][0].update(receipt=b['cases'][0]['runs'][0]['receipt'] + 'x')),
    ('reused_job', lambda b: b['cases'][1]['runs'][0].update(job=b['cases'][0]['runs'][0]['job'])),
    ('synthetic', lambda b: b['cases'][0]['runs'][0]['job'].update(synthetic=True)),
    ('replayed', lambda b: b['cases'][0]['runs'][0]['job']['result']['steps'][0].update(replayed=True)),
])
def test_tampered_repeats_provenance_is_refused_even_when_resigned(roots, tmp_path, label, mutate):
    root, runtime = roots
    name = TOOLS[1]
    package = load_package(name, root)
    path, _ = collect(root, runtime, name, tmp_path / 'out')
    data = json.loads(path.read_text())
    source = path.parent / data['provenance']['path']
    bundle = json.loads(source.read_text())
    mutate(bundle)
    source.write_text(json.dumps(bundle, sort_keys=True))
    data['provenance']['sha256'] = reliability.digest(bundle)
    path.write_text(json.dumps(data, sort_keys=True))
    with pytest.raises(ValueError):
        reliability.verify_repeats(path, package, root)


def test_forged_summary_and_unsigned_edit_are_refused(roots, tmp_path):
    root, runtime = roots
    name = TOOLS[0]
    package = load_package(name, root)
    path, _ = collect(root, runtime, name, tmp_path / 'out')
    data = json.loads(path.read_text())
    data['repeats'][0]['verdicts'][0] = 'forged'
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='differs from preserved run provenance'):
        reliability.verify_repeats(path, package, root)
    data = json.loads(path.read_text())
    source = path.parent / data['provenance']['path']
    source.write_text(source.read_text() + ' ')
    with pytest.raises(ValueError):
        reliability.verify_repeats(path, package, root)


def test_model_agents_and_undeclared_agents_cannot_use_repeats_only_evidence(roots, tmp_path):
    root, runtime = roots
    with pytest.raises(ValueError, match='tool agents'):
        reliability.produce_repeats('knowledge.source_classify', {'0': ['AOS-2026-90001']}, root, runtime, tmp_path, runtime_class='x')
    with pytest.raises(ValueError, match='no reliability verdict is declared'):
        job = fx.add_job(root, runtime, 99, ('marketing.post_check',))
        reliability.produce_repeats('marketing.post_check', {'0': [job['id']]}, root, runtime, tmp_path, runtime_class='x')


def test_full_collector_still_requires_both_probes(roots):
    root, runtime = roots
    name = TOOLS[1]
    _, ids = collect(root, runtime, name, runtime / 'scratch')
    with pytest.raises(KeyError):
        reliability.produce(name, ids, {}, {}, root, runtime)
