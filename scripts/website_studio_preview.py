#!/usr/bin/env python3
"""Start/reuse only the isolated Website Studio dev preview. Never controls TTROS services.
Run from this development worktree: python3 scripts/website_studio_preview.py
"""
import argparse
import signal
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
STATE=ROOT/'.studio-preview'

def listener(port):
    for line in subprocess.check_output(['ss','-ltnp'],text=True).splitlines():
        if re.search(rf'127\.0\.0\.1:{port}\b',line):
            match=re.search(r'pid=(\d+)',line)
            if not match:raise RuntimeError(f'Port {port} has an unidentified owner; refusing to change it.')
            pid=int(match[1]);cwd=Path(f'/proc/{pid}/cwd').resolve()
            if not cwd.is_relative_to(ROOT):raise RuntimeError(f'Port {port} belongs to another checkout; refusing to change it.')
            return pid,cwd
    return None

def start(name,port,command,cwd,env):
    found=listener(port)
    if found:
        if found[1]!=cwd:raise RuntimeError(f'{name} port {port} serves a different fixture: {found[1]}. No process was stopped.')
        return found[0]
    with (STATE/(name+'.log')).open('ab') as log:
        process=subprocess.Popen(command,cwd=cwd,env={**os.environ,**env},stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    for _ in range(100):
        if process.poll() is not None:raise RuntimeError(f'{name} exited; see {STATE/(name+".log")}')
        try:
            with socket.create_connection(('127.0.0.1',port),timeout=.1):
                owner=listener(port)
                if owner:return owner[0]
        except OSError:time.sleep(.1)
    raise RuntimeError(f'{name} did not become ready; see its local log.')

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--refresh-site',action='store_true',help='Switch this helper’s owned local Next preview to the selected built fixture.')
    args=parser.parse_args()
    if ROOT==Path('/opt/ttros-core'):raise RuntimeError('Run this helper in the isolated development worktree.')
    STATE.mkdir(exist_ok=True)
    pointer=STATE/'fixture-path'
    if not pointer.exists():pointer=ROOT/'.stage4-runtime/fixture-path'
    fixture=Path(pointer.read_text().strip()).resolve()
    if not fixture.is_relative_to(ROOT) or not (fixture/'.next/BUILD_ID').is_file():raise RuntimeError('Build a worktree-local Next fixture before starting its preview.')
    existing=listener(4361)
    if args.refresh_site and existing and existing[1]!=fixture:
        recorded=json.loads((STATE/'processes.json').read_text())
        if recorded['processes']['site']!=existing[0]:raise RuntimeError('Next listener changed ownership; no process was stopped.')
        os.kill(existing[0],signal.SIGTERM)
        for _ in range(50):
            if not listener(4361):break
            time.sleep(.1)
        else:raise RuntimeError('Previous local Next preview has not stopped; no second process was started.')
    processes={
      'api':start('api',4351,[str(ROOT/'.stage4-runtime/venv/bin/python'),'-m','uvicorn','studio_local:app','--host','127.0.0.1','--port','4351','--reload','--reload-dir','dashboard/backend'],ROOT,{'PYTHONPATH':'tools:dashboard/backend','TTROS_STUDIO_OFFLINE':'1','TTROS_STUDIO_STATE':str(STATE/'state')}),
      'editor':start('editor',4350,['npm','run','dev','--','--port','4350','--strictPort'],ROOT/'dashboard/frontend',{'TTROS_STUDIO_API_TARGET':'http://127.0.0.1:4351'}),
      'site':start('site',4361,['npm','run','start','--','--hostname','127.0.0.1','--port','4361'],fixture,{'NEXT_TELEMETRY_DISABLED':'1'}),
    }
    with urllib.request.urlopen('http://127.0.0.1:4351/api/website/studio/local-mode',timeout=5) as response:
        if json.load(response).get('offline') is not True:raise RuntimeError('Preview API is not confirmed offline; no model request was made.')
    result={'processes':processes,'fixture':str(fixture),'editor':'http://127.0.0.1:4350/websites?tab=studio&site=default-site','site':'http://127.0.0.1:4361','models':'disabled','production':'untouched'}
    (STATE/'processes.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))

if __name__=='__main__':main()
