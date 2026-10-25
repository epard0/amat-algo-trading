#!/usr/bin/env python3
"""One finite read-only observation, reconciliation and immutable dashboard publication."""
import datetime as dt
import fcntl
import hashlib
import json
import pathlib
import shlex
import sys
from zoneinfo import ZoneInfo

BASE = pathlib.Path(__file__).resolve().parents[2]
sys.path[:0] = [str(BASE/'frontend'),str(BASE/'executor/src')]
from server.remote import load_config
from executor.remote import SSHRunner
from server.paper_session import ROOT, reconcile

folder=BASE/ROOT;folder.mkdir(parents=True,exist_ok=True)
protocol=json.loads((folder/'protocol.json').read_text())
if hashlib.sha256(json.dumps(protocol,sort_keys=True,allow_nan=False).encode()).hexdigest() != (folder/'protocol.sha256').read_text().strip():raise ValueError('Unsealed monitor protocol')
for relative,expected in protocol['source_sha256'].items():
    if hashlib.sha256((BASE/relative).read_bytes()).hexdigest()!=expected:raise ValueError('Monitor source differs from sealed protocol')
def atomic(path,value):
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(value,sort_keys=True,indent=2,allow_nan=False)+'\n');tmp.replace(path)
def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,allow_nan=False).encode()).hexdigest()
with (folder/'writer.lock').open('w') as lock:
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    cfg=load_config(BASE/'frontend/.runtime/remote.toml');cfg['command_timeout']=60;cfg['max_output_bytes']=524288
    runner=SSHRunner(cfg);client=runner.connect()
    try:
        code=(BASE/'frontend/scripts/paper-session-remote.py').read_text()
        result=runner.execute_command(client,'cd /tmp && python3 -I -c '+shlex.quote(code),'paper_session_observation')
        if result['exit_code'] or result['truncated']:raise RuntimeError('Remote observation incomplete')
        observation=json.loads(result['stdout'])
    finally:client.close()
    from server.paper_session import project
    now=dt.datetime.now(dt.UTC)
    previous=json.loads((folder/'state.json').read_text()) if (folder/'state.json').exists() else None
    publication, old=project(observation,now,previous,'2026-10-21')
    status=publication['status'];audit=publication['audit'];perps_fresh=publication['perps_fresh']
    name='observation-'+now.strftime('%Y%m%dT%H%M%S%f')+'.json';atomic(folder/name,publication)
    atomic(folder/'latest.json',{'file':name,'sha256':digest(publication)})
    atomic(folder/'state.json',old)
    # Private source evidence has no account number, broker account id or credentials.
    evidence=folder/'evidence';evidence.mkdir(exist_ok=True);atomic(evidence/name,observation)
    print(json.dumps({'status':status,'observed_at':now.isoformat(),'audit':audit,'perps_fresh':perps_fresh,'publication':name}))
