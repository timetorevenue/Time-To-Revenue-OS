#!/usr/bin/env python3
"""Start isolated, offline TTROS account/website previews. No production control.

Run in the feature checkout: python3 scripts/connections_preview.py
Dependencies: isolated .venv and Dashboard node_modules. Secrets are not loaded.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import time
import urllib.request
import argparse

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / '.studio-preview'


def owner(port):
    for line in subprocess.check_output(['ss', '-ltnp'], text=True).splitlines():
        if re.search(rf'127\.0\.0\.1:{port}\b', line):
            match = re.search(r'pid=(\d+)', line)
            if not match:
                raise RuntimeError(f'Port {port} has an unidentified owner')
            pid = int(match[1])
            cwd = Path(f'/proc/{pid}/cwd').resolve()
            if not cwd.is_relative_to(ROOT):
                raise RuntimeError(f'Port {port} belongs to {cwd}; no process was changed')
            return pid
    return None


def start(name, port, cmd, cwd, env):
    pid = owner(port)
    if pid:
        return pid
    with (STATE / (name + '.log')).open('ab') as log:
        proc = subprocess.Popen(cmd, cwd=cwd, env={**os.environ, **env}, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    for _ in range(80):
        if proc.poll() is not None:
            raise RuntimeError(f'{name} failed; see {STATE / (name + ".log")}')
        try:
            pid = owner(port)
        except RuntimeError as exc:
            # During bind(), ss can briefly show the socket before its PID.
            # Wait only for the process we just launched; never change a port.
            if 'unidentified owner' not in str(exc):
                raise
            pid = None
        if pid:
            return pid
        time.sleep(.15)
    raise RuntimeError(f'{name} is not ready')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--operator-connections', action='store_true', help='Enable the existing Composio human account for the local operator only. No credentials are read by this launcher.')
    args = parser.parse_args()
    if ROOT == Path('/opt/ttros-core'):
        raise RuntimeError('Use the isolated feature checkout')
    STATE.mkdir(exist_ok=True)
    # Explicitly offline: even an inherited provider key cannot cause a model
    # request. External account consent is disabled for local_preview identity.
    common = {'TTROS_STUDIO_OFFLINE': '1', 'TTROS_STUDIO_STATE': str(STATE / 'state'),
              'TTROS_DASHBOARD_ORIGIN': os.environ.get('TTROS_DASHBOARD_ORIGIN', 'http://localhost:4390'), 'PYTHONPATH': 'tools:dashboard/backend'}
    if args.operator_connections:
        common.update(TTROS_CONNECTIONS_LOCAL_OPERATOR='1', TTROS_CONNECTIONS_OPERATOR_EMAIL='account-owner@example.invalid')
    api = start('connections-api', 4391, [str(ROOT / '.venv/bin/python'), '-m', 'uvicorn', 'studio_local:app', '--host', '127.0.0.1', '--port', '4391'], ROOT, common)
    editor = start('connections-dashboard', 4390, ['npm', 'run', 'dev', '--', '--port', '4390', '--strictPort'], ROOT / 'dashboard/frontend',
                   {'TTROS_API_TARGET': 'http://127.0.0.1:4391', 'TTROS_STUDIO_API_TARGET': 'http://127.0.0.1:4391'})
    with urllib.request.urlopen('http://127.0.0.1:4391/api/website/studio/local-mode', timeout=5) as response:
        if json.load(response).get('offline') is not True:
            raise RuntimeError('API is not confirmed offline')
    origin = common['TTROS_DASHBOARD_ORIGIN']
    result = {'api_pid': api, 'dashboard_pid': editor, 'studio': origin + '/websites?tab=studio&site=default-site',
              'connections': origin + '/settings/connections', 'models': 'disabled', 'production': 'untouched'}
    (STATE / 'connections-processes.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
