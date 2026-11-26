"""Reproducible offline before/after benchmark. No provider calls or canonical writes.

Run with the backend interpreter and backend/root pytest site-packages on PYTHONPATH.
--revision loads exact local Git source bytes; it does not change any checkout.
"""
import argparse
import cProfile
import importlib.util
import io
import json
import math
from pathlib import Path
import pstats
import statistics
import subprocess
import sys
import tempfile
import time
import types

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'tools'),str(ROOT)]
parser = argparse.ArgumentParser()
parser.add_argument('--revision')
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
if args.revision:
    for name in ('business_brain_scope','aos_indexer','aos_entity_index','shared_brain_read','aos_david'):
        source = subprocess.check_output(['git','-C',str(ROOT),'show',f'{args.revision}:tools/{name}.py'],text=True)
        module = types.ModuleType(name)
        module.__file__ = str(ROOT/'tools'/f'{name}.py')
        sys.modules[name] = module
        exec(compile(source,module.__file__,'exec'),module.__dict__)

from tests.new_david_fixture import corpus
from tests.test_david_m1 import Port, Threads, reply
import aos_david
import shared_brain_read


def measure(call, n):
    samples=[]
    result=None
    for _ in range(n):
        start=time.perf_counter();result=call();samples.append((time.perf_counter()-start)*1000)
    return {'samples':n, 'p50_ms':statistics.median(samples), 'p95_ms':sorted(samples)[math.ceil(n*.95)-1]},result

with tempfile.TemporaryDirectory() as temp, corpus(Path(temp)) as (vault,db,registry):
    # Warm imports and checked schema equally; no process/startup time hidden in warm figures.
    assert len(shared_brain_read.search('Callback')['matches']) == 7
    retrieval, found = measure(lambda: shared_brain_read.search('Callback'),60)
    port = Port(david=[r for _ in range(40) for r in (
        reply(action='lookup',query='Callback',topic='callbacks'),
        reply(reply='Fixture sourced reply.',topic='callbacks'))])
    david = aos_david.David(Path(temp),model=port,threads=Threads())
    response, result = measure(lambda: david.turn('benchmark','Recall callbacks'),40)
    prompt_sizes=[len(p.encode()) for agent,_,p in port.prompts if agent=='david']
    profiler=cProfile.Profile();profiler.enable()
    for _ in range(30):shared_brain_read.search('Callback')
    profiler.disable()
    stream=io.StringIO();pstats.Stats(profiler,stream=stream).sort_stats('cumulative').print_stats(22)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.with_suffix('.profile.txt').write_text(stream.getvalue().rstrip()+'\n')
    metrics={'revision':args.revision or 'working candidate','environment':'isolated Linux fixture, warm, scripted ModelPort, no provider',
             'retrieval':retrieval,'response':response,'context':{'max_bytes':max(prompt_sizes),'max_estimated_tokens':math.ceil(max(prompt_sizes)/4),'method':'UTF-8 bytes / 4 estimate; provider tokenization unavailable'},
             'model_calls_per_response':result['model_calls'],'results':found}
    if hasattr(aos_david, 'history_window'):
        history,last=measure(lambda:david.turn('benchmark','/history 2026-11-15'),60)
        metrics['deterministic_history']={**history,'model_calls':last['model_calls'],'matches':len(last['evidence'])}
    args.output.write_text(json.dumps(metrics,indent=2)+'\n')
    print(json.dumps({key:value for key,value in metrics.items() if key!='results'},indent=2))
