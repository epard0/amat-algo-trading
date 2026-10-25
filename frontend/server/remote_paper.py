"""Remote-only paper evidence, cached for display without driving collection."""
from __future__ import annotations
import datetime as dt
import json
import shlex
import threading
import time
from pathlib import Path

from server.remote import Runner,config_path,load_config,safe_output


def invoke(path:Path,session:str|None=None)->dict:
    if session is not None and dt.date.fromisoformat(session).isoformat()!=session:
        raise ValueError('Invalid paper session date')
    cfg=load_config(path);cfg['max_output_bytes']=2_000_000;cfg['command_timeout']=15
    # Configuration loader caps normal output at1MiB; this projection stays below that.
    cfg['max_output_bytes']=1_048_576
    argv=[f"/home/{cfg['username']}/.local/bin/amat-paper-monitor-read"]
    if session:argv.append(session)
    result=safe_output(Runner(cfg).run(shlex.join(argv)),cfg)
    if result['exit_code'] or result['truncated']:raise RuntimeError('Remote monitor read failed')
    value=json.loads(result['stdout'])
    if value.get('schema')!='paper-session/1' or value.get('execution_authority')!='none' or value.get('collection_location')!='remote VPS':
        raise ValueError('Unverified remote monitor publication')
    if session and value.get('target_session')!=session:raise ValueError('Remote session mismatch')
    return value


class RemotePaperSession:
    def __init__(self,service):
        self.service=service;self.lock=threading.Lock();self.cache={};self.attempts={}
        with service.lock:
            row=service.db.execute("SELECT data FROM observations WHERE name='remote.paper-session'").fetchone()
        if row:self.cache['latest']=json.loads(row['data'])

    def describe(self,session=None):
        if session is not None and dt.date.fromisoformat(session).isoformat()!=session:
            raise ValueError('Invalid paper session date')
        key=session or 'latest'
        with self.lock:
            if time.monotonic()-self.attempts.get(key,0)<15 and key in self.cache:return self.cache[key]
            self.attempts[key]=time.monotonic()
            try:
                value=invoke(config_path(self.service.storage),session)
                self.cache[key]=value
                if not session:
                    with self.service.lock,self.service.db:
                        self.service.db.execute("INSERT OR REPLACE INTO observations(name,data) VALUES (?,?)",('remote.paper-session',json.dumps(value)))
            except (OSError,ValueError,RuntimeError) as exc:
                old=self.cache.get(key,{})
                self.cache[key]={**old,'status':old.get('status','unavailable'),'transport':'unavailable','stale':True,
                    'collection_location':'remote VPS','message':'Remote paper observations could not be read. Last known measurements retained.',
                    'transport_error':str(exc)}
            # Date selection cannot grow process memory without bound.
            if len(self.cache)>32:
                for previous in list(self.cache):
                    if previous not in ('latest',key):self.cache.pop(previous,None);self.attempts.pop(previous,None);break
            return self.cache[key]
