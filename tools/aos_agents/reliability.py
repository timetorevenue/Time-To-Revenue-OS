"""Gate 7 evidence production from runtime observations, never from summary claims.

This collector does not run jobs or probes. After separately authorized qualification,
pass five case -> three distinct aos_jobs IDs and the worker probe's raw observations.
It copies hashed sources beside the evidence so repacking preserves the audit trail.
Offline probes must set simulated=true and can never qualify a release.

Repeat agreement (schema ttros-reliability-2) compares each run's DECLARED verdict
(verdicts.py, agents/reliability_verdicts.json), recomputed from the run's full journaled
output after that output is checked against the receipt-attested output_sha256. The full
output hashes stay in the evidence. Version-1 provenance compared whole-output hashes and is
refused: recollect it.

Every model ledger row carries ModelPort's transport provenance. Repeats and the kill probe
must have run on the production Codex binary. The forced failure declares its evidence class:
'genuine_provider' (the real binary, a real provider stop) or 'injected_transport' (the real
aos_jobs/ModelPort/journal path with a stand-in provider process). The class must agree with
the ledger's transport provenance, and the forced stop must come after a launched call.
Which classes gate 7 accepts is release.py's decision, not the collector's.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from . import evals, verdicts
from .package import load_package

SCHEMA = 'ttros-reliability-2'
FORCED_EVIDENCE_CLASSES = ('genuine_provider', 'injected_transport')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def launched_model_calls(attempts: list[dict]) -> list[str]:
    """Keys of model calls that may have reached a provider, from a job's model-attempt journal.

    Each call is journaled 'started' before launch. A later 'not_launched' record with the same
    key closes one such start: ModelPort refused before any provider call (policy, token fuse or
    no eligible Codex account, after which aos_jobs requeues the job). Any other status, or a
    close without a start, makes the journal invalid."""
    calls: list[str] = []
    for r in attempts:
        status = (r.get('result') or {}).get('status')
        if status not in ('started', 'not_launched'):
            raise ValueError('invalid model attempt journal')
        key = digest([r['agent'], r['step'], r['input_sha256'], r['package_sha256']])
        if status == 'started':
            calls.append(key)
        elif key in calls:
            calls.remove(key)
        else:
            raise ValueError('model attempt closed before it started')
    return calls

def _transport(row):
    """ModelPort's record of which Codex binary ran: True production, False stand-in, None unknown."""
    transport = (row.get('model_selection') or {}).get('transport')
    if not isinstance(transport, dict) or not isinstance(transport.get('production_binary'), bool):
        return None
    return transport['production_binary']


def _require_production_transport(rows, what):
    for row in rows:
        production = _transport(row)
        if production is None:
            raise ValueError(f'{what} ledger row has no transport provenance')
        if not production:
            raise ValueError(f'{what} ran on a stand-in transport, not the production Codex binary')


def _only_step(job, package):
    steps = [s for s in (job.get('result') or {}).get('steps') or [] if s.get('agent') == package.ref]
    if len(steps) != 1:
        raise ValueError(f'{package.ref} ran {len(steps)} times in job {job.get("id")}; its verdict is ambiguous')
    return steps[0]


def summarize_repeats(bundle, package, root):
    """Recompute every repeat verdict from preserved observations: (repeats, verdict_spec).

    The repeat half of ``summarize``: identity, receipts, hashes and provenance of each run.
    It knows nothing of the kill or forced-failure probes."""
    from .runtime import canonical_sha256
    if bundle.get('schema') == 'ttros-reliability-1':
        raise ValueError('version-1 provenance compared whole-output hashes; recollect with declared verdicts')
    if bundle.get('schema') != SCHEMA:
        raise ValueError('unsupported reliability provenance schema')
    declared = verdicts.declaration(package, root)
    ident = {'agent': package.ref, 'package_sha256': package.content_sha256, 'binding': evals.binding(package)}
    seen, repeats = set(), []
    for case in bundle['cases']:
        keys, decisions, outputs = [], [], []
        for run in case['runs']:
            if run['job']['id'] in seen:
                raise ValueError('reused reliability job')
            seen.add(run['job']['id'])
            job, receipt = run['job'], run['receipt']
            if any(job.get(k) for k in ('simulated', 'synthetic', 'fixture')):
                raise ValueError('simulated runtime provenance')
            if hashlib.sha256(receipt.encode()).hexdigest() != run['receipt_sha256']:
                raise ValueError('receipt hash mismatch')
            if not any(r.get('sha256') == run['receipt_sha256'] for r in job.get('receipts', [])):
                raise ValueError('receipt not pinned by job')
            step = _only_step(job, package)
            actual = step.get('execution_identity') or {}
            if any(actual.get(k) != v for k, v in ident.items()) or step.get('replayed'):
                raise ValueError('missing executed package identity or replayed run')
            identities = [json.loads(line[len('Execution identity: '):]) for line in receipt.splitlines() if line.startswith('Execution identity: ')]
            if not any(all(i.get(k) == actual.get(k) for k in (*ident, 'input_sha256', 'output_sha256')) for i in identities):
                raise ValueError('receipt lacks execution attestation')
            if step.get('status') != 'ok' or job.get('status') != 'done':
                raise ValueError('reliability repeat did not complete')
            if package.kind != 'tool':
                ledger = run['ledger']
                if not ledger or not job['usage'].get('exact'):
                    raise ValueError('missing exact model provenance')
                for row in ledger:
                    select = row.get('model_selection') or {}
                    if row.get('synthetic') or row.get('item_id') != job['id'] or (row.get('actual_model') or select.get('actual_model')) != package.model['id'] or select.get('reasoning_effort') != package.model['effort']:
                        raise ValueError('model provenance mismatch')
                _require_production_transport(ledger, 'reliability repeat')
            if 'output' not in run or canonical_sha256(run['output']) != actual.get('output_sha256'):
                raise ValueError('preserved output does not match the attested output_sha256')
            verdict = verdicts.extract(package, root, run['output'], declared=declared)
            keys.append(verdicts.key(verdict))
            decisions.append(verdict)
            outputs.append(actual['output_sha256'])
        if len({_only_step(r['job'], package)['execution_identity'].get('input_sha256') for r in case['runs']}) != 1:
            raise ValueError('repeat inputs differ')
        repeats.append({'case': case['case'], 'verdicts': keys, 'decisions': decisions, 'output_sha256': outputs})
    spec = {'path': verdicts.DECLARATIONS_PATH.as_posix(), 'spec_sha256': declared['spec_sha256'], 'fields': declared['fields']}
    return repeats, spec


def summarize(bundle, package, root):
    """Recompute all verdicts and probe counters from preserved observations."""
    repeats, spec = summarize_repeats(bundle, package, root)
    kill, forced = bundle['kill'], bundle['forced']
    if any(p.get('simulated') or p.get('synthetic') or p.get('fixture') for p in (kill, forced)):
        raise ValueError('simulated probe provenance')
    return {'repeats': repeats, 'verdict_spec': spec, **probe_summary(kill, forced, package)}


def journaled_output(runtime_root, job_id, package, output_sha256):
    """The full output of one step from the job's durable step journal (planner, plan and direct-agent runs)."""
    from .journal import StepJournal
    from .runtime import canonical_sha256
    matches = [row['result']['output'] for row in StepJournal(runtime_root, job_id).records()
               if row.get('agent') == package.ref and isinstance(row.get('result'), dict)
               and canonical_sha256(row['result'].get('output')) == output_sha256]
    if not matches:
        raise ValueError(f'{job_id}: {package.ref} output is not in the step journal')
    return matches[0]



def collect_cases(package, cases, runtime_root: Path, ledger: list[dict]) -> list[dict]:
    """Preserved observations of existing repeat jobs: job record, receipt, full journaled output, ledger rows."""
    import aos_jobs
    collected = []
    for case, ids in cases.items():
        runs = []
        for job_id in ids:
            job = aos_jobs.load(runtime_root, job_id)
            receipt = job['receipts'][-1]
            path = (runtime_root / receipt['path']).resolve()
            if not path.is_relative_to(runtime_root):
                raise ValueError('receipt escapes runtime')
            step = _only_step(job, package)
            output = journaled_output(runtime_root, job_id, package, (step.get('execution_identity') or {}).get('output_sha256'))
            runs.append({'job': job, 'receipt': path.read_text(), 'receipt_sha256': receipt['sha256'], 'output': output,
                         'ledger': [r for r in ledger if r.get('item_id') == job_id and package.ref in str(r.get('surface'))]})
        collected.append({'case': case, 'runs': runs})
    return collected


def _read_ledger(runtime_root: Path) -> list[dict]:
    path = runtime_root / 'queue/token_ledger.jsonl'
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


REPEATS_SUFFIX = '.repeats.json'


def produce_repeats(agent, cases, root: Path, runtime_root: Path, out_dir: Path, *, runtime_class: str):
    """Collect only the repeat half of gate 7 (a tool agent, zero model calls) from existing jobs.

    Same per-run verification as ``produce``. The kill and forced-failure probes are NOT part of
    this evidence, so it is written outside the package under a name release.gate7 never reads
    (``*.repeats.json``) and declares ``gate7_eligible: false``. It qualifies nothing."""
    package = load_package(agent, root)
    if package.kind != 'tool':
        raise ValueError('repeats-only evidence is for tool agents; a model agent needs live repeats')
    verdicts.declaration(package, root)
    runtime_root = Path(runtime_root).resolve()
    bundle = {'schema': SCHEMA, 'runtime_class': runtime_class,
              'cases': collect_cases(package, cases, runtime_root, _read_ledger(runtime_root))}
    repeats, spec = summarize_repeats(bundle, package, root)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f'{package.name}.{evals.binding(package)}'
    source = out_dir / f'{stem}.repeats.provenance.json'
    path = out_dir / f'{stem}{REPEATS_SUFFIX}'
    data = {'agent': package.ref, 'binding': evals.binding(package), 'package_sha256': package.content_sha256,
            'runtime_class': runtime_class, 'gate7_eligible': False, 'production_qualification': False,
            'missing_for_gate7': ['kill -9 / resume probe', 'forced post-launch quota/auth failure probe (genuine_provider)'],
            'repeats': repeats, 'verdict_spec': spec, 'provenance': {'path': source.name, 'sha256': digest(bundle)}}
    source.write_text(json.dumps(bundle, sort_keys=True, indent=1) + '\n')
    path.write_text(json.dumps(data, sort_keys=True, indent=1) + '\n')
    return path


def verify_repeats(path: Path, package, root):
    """Re-derive repeats-only evidence from its preserved provenance; raises on any difference."""
    path = Path(path)
    data = json.loads(path.read_text())
    source = (path.parent / str((data.get('provenance') or {}).get('path', ''))).resolve()
    if not source.is_relative_to(path.parent.resolve()) or source.suffix != '.json':
        raise ValueError('invalid repeats provenance path')
    bundle = json.loads(source.read_text())
    if digest(bundle) != data['provenance'].get('sha256') or bundle.get('simulated'):
        raise ValueError('altered or simulated repeats provenance')
    repeats, spec = summarize_repeats(bundle, package, root)
    if data.get('repeats') != repeats or data.get('verdict_spec') != spec:
        raise ValueError('repeats summary differs from preserved run provenance')
    return data


def produce(agent, cases, kill, forced, root: Path, runtime_root: Path, *, simulated=False):
    """Collect existing jobs only; never allocates a queue, executes a model or promotes."""
    package = load_package(agent, root)
    verdicts.declaration(package, root)   # an agent without a declared verdict cannot supply gate 7 at all
    runtime_root = Path(runtime_root).resolve()
    ledger = _read_ledger(runtime_root)
    bundle = {'schema': SCHEMA, 'simulated': simulated, 'cases': collect_cases(package, cases, runtime_root, ledger),
              'kill': kill, 'forced': forced}
    summary = summarize(bundle, package, root)
    path = package.directory / 'evidence' / f'{evals.binding(package)}.reliability.json'
    source = path.with_suffix('.provenance.json')
    data = {'agent': package.ref, 'binding': evals.binding(package), 'package_sha256': package.content_sha256,
            'prompt_sha256': package.model.get('prompt_sha256'), 'mode': 'offline' if simulated else 'live',
            'simulated': simulated, **summary, 'provenance': {'path': source.name, 'sha256': digest(bundle)}}
    source.write_text(json.dumps(bundle, sort_keys=True, indent=1) + '\n')
    path.write_text(json.dumps(data, sort_keys=True, indent=1) + '\n')
    return path


def verify(data, package, path, root):
    pin = data.get('provenance') or {}
    source = (path.parent / str(pin.get('path', ''))).resolve()
    if not source.is_relative_to(path.parent.resolve()) or source.suffix != '.json':
        raise ValueError('invalid reliability provenance path')
    bundle = json.loads(source.read_text())
    if digest(bundle) != pin.get('sha256') or bundle.get('simulated'):
        raise ValueError('altered or simulated reliability provenance')
    actual = summarize(bundle, package, root)
    if any(data.get(k) != v for k, v in actual.items()):
        raise ValueError('reliability summary differs from preserved run provenance')


def _runtime_sources(root, job_id):
    import aos_jobs
    root = Path(root).resolve()
    job = aos_jobs.load(root, job_id)
    if not job.get('receipts'):
        raise ValueError(f'{job_id} has no receipt: it never settled (a pre-launch refusal is requeued, not a probe)')
    receipt = job['receipts'][-1]
    path = (root / receipt['path']).resolve()
    if not path.is_relative_to(root):
        raise ValueError('probe receipt escapes runtime')
    text = path.read_text()
    ledger = root / 'queue/token_ledger.jsonl'
    rows = [json.loads(line) for line in ledger.read_text().splitlines() if line.strip()] if ledger.exists() else []
    from .journal import StepJournal
    attempts = StepJournal(root, job_id).path.with_suffix('.model-attempts.jsonl')
    call_attempts = [json.loads(line) for line in attempts.read_text().splitlines() if line.strip()] if attempts.exists() else []
    return {'job': job, 'receipt': text, 'receipt_sha256': receipt['sha256'], 'call_attempts': call_attempts,
            'ledger': [row for row in rows if row.get('item_id') == job_id]}


def capture_sigkill(process, root, job_id, resume, *, simulated=False):
    """Kill ONLY an owned qualification subprocess, then resume via the aos_jobs owner.

    Caller launches an explicitly authorized worker with its existing job ID and
    calls here at the chosen checkpoint. Never use a service PID. ``resume`` must
    use aos_jobs claim/recovery; this function allocates no job or queue.
    Not called by readiness checks or evidence collection.
    """
    from .journal import StepJournal
    checkpoint = StepJournal(Path(root), job_id).path.read_text()
    if not checkpoint.strip() or process.poll() is not None:
        raise ValueError('worker is not running at a durable checkpoint')
    process.kill()
    code = process.wait(timeout=10)
    resume(job_id)
    return {'returncode': code, 'checkpoint_before_kill': checkpoint,
            'checkpoint_sha256': hashlib.sha256(checkpoint.encode()).hexdigest(),
            'source': _runtime_sources(root, job_id), 'simulated': simulated}


def capture_forced_failure(root, job_id, kind, *, evidence_class, simulated=False):
    """Collect the completed forced-failure job; the authorized driver causes the fault.

    ``evidence_class`` says how: 'genuine_provider' (the production Codex binary reported a
    real quota/auth stop) or 'injected_transport' (a stand-in provider process on the real
    aos_jobs/ModelPort/journal path). It is checked against the ledger's transport provenance."""
    if kind not in ('auth', 'quota'):
        raise ValueError('expected auth or quota fault')
    if evidence_class not in FORCED_EVIDENCE_CLASSES:
        raise ValueError(f'evidence_class must be one of {FORCED_EVIDENCE_CLASSES}')
    return {'kind': kind, 'evidence_class': evidence_class, 'source': _runtime_sources(root, job_id), 'simulated': simulated}


def _check_probe_source(source, package):
    sha = hashlib.sha256(source['receipt'].encode()).hexdigest()
    if sha != source['receipt_sha256'] or not any(r.get('sha256') == sha for r in source['job']['receipts']):
        raise ValueError('probe receipt provenance mismatch')
    if any(source['job'].get(k) for k in ('simulated', 'synthetic', 'fixture')):
        raise ValueError('simulated probe job')
    step = _only_step(source['job'], package)
    ident = step.get('execution_identity') or {}
    if ident.get('package_sha256') != package.content_sha256 or ident.get('binding') != evals.binding(package):
        raise ValueError('probe ran another package binding')
    if package.kind != 'tool' and not source['ledger'] and not source['job'].get('failure_class'):
        raise ValueError('probe has no model provenance')
    if any(r.get('synthetic') or r.get('item_id') != source['job']['id'] for r in source['ledger']):
        raise ValueError('invalid probe ledger provenance')


def forced_failure_summary(forced, package):
    """Derive the forced-failure facts from its captured sources; the class must match the transport."""
    _check_probe_source(forced['source'], package)
    failed = forced['source']['job']
    statuses = [s.get('status') for s in failed.get('result', {}).get('steps', [])]
    evidence_class = forced.get('evidence_class')
    if evidence_class not in FORCED_EVIDENCE_CLASSES:
        raise ValueError('forced failure has no declared evidence class')
    transports = [_transport(row) for row in forced['source']['ledger']]
    if not transports or None in transports:
        raise ValueError('forced failure ledger lacks transport provenance')
    if (evidence_class == 'genuine_provider') != all(transports):
        raise ValueError(f'forced failure evidence class {evidence_class!r} contradicts its transport provenance')
    return {'kind': forced['kind'], 'evidence_class': evidence_class,
            'post_launch': bool(launched_model_calls(forced['source'].get('call_attempts') or [])),
            'outcome': 'blocked' if 'blocked' in statuses else failed['status'],
            'truthful': failed.get('failure_class') in ({'quota_unavailable', 'quota_exhausted'} if forced['kind'] == 'quota'
                                                          else {'authentication_unavailable', 'needs_authentication'}),
            'billed_fallback': any(r.get('provider') != 'openai-codex' or not str(r.get('surface', '')).startswith('modelport:codex')
                                   for r in forced['source']['ledger'])}


def probe_summary(kill, forced, package):
    """Verify probe source receipts and derive counters; reject summary-only probe claims."""
    _check_probe_source(kill['source'], package)
    forced_facts = forced_failure_summary(forced, package)
    if hashlib.sha256(kill['checkpoint_before_kill'].encode()).hexdigest() != kill['checkpoint_sha256']:
        raise ValueError('checkpoint hash mismatch')
    records = [json.loads(line) for line in kill['checkpoint_before_kill'].splitlines() if line.strip()]
    if not records or not any(r.get('step') and (r.get('result') or {}).get('status') == 'ok' for r in records):
        raise ValueError('kill checkpoint has no completed step')
    resumed = kill['source']['job']
    if any(e.get('status') in ('failed', 'uncertain', 'intent') for e in resumed.get('effects', [])):
        raise ValueError('kill resume has unresolved effects')
    if resumed.get('effects') and 'effect_attempts' not in resumed:
        raise ValueError('kill resume has no durable effect attempt provenance')
    effects = [e['key'] for e in resumed.get('effect_attempts', [])]
    attempts = kill['source'].get('call_attempts')
    if package.kind != 'tool' and not attempts:
        raise ValueError('no durable model-attempt provenance for kill probe')
    calls = launched_model_calls(attempts or [])
    _require_production_transport(kill['source']['ledger'], 'kill probe')
    return {'kill_resume': {'signal': 'SIGKILL' if kill['returncode'] == -9 else 'unknown',
                            'resumed': resumed['status'] == 'done', 'duplicate_effects': len(effects)-len(set(effects)),
                            'repeated_model_calls': len(calls)-len(set(calls))},
            'forced_failure': forced_facts}
