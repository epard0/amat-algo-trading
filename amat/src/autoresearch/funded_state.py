"""Durable best-run history shared by funded research campaigns and visual reports."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import time
from pathlib import Path


DDL = """
CREATE TABLE IF NOT EXISTS campaigns(
 id TEXT PRIMARY KEY, root TEXT NOT NULL UNIQUE, protocol_sha256 TEXT NOT NULL,
 protocol_json TEXT NOT NULL, registered_at_ns INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS best_events(
 id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL REFERENCES campaigns(id),
 batch INTEGER NOT NULL, policy_sha256 TEXT NOT NULL, policy_json TEXT NOT NULL,
 training_score REAL NOT NULL, evidence_path TEXT NOT NULL, evidence_sha256 TEXT NOT NULL,
 recorded_at_ns INTEGER NOT NULL, UNIQUE(campaign_id,batch));
CREATE TABLE IF NOT EXISTS snapshots(
 id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL REFERENCES campaigns(id),
 data_json TEXT NOT NULL, evidence_sha256 TEXT NOT NULL, recorded_at_ns INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS evidence_objects(sha256 TEXT PRIMARY KEY, content BLOB NOT NULL);
CREATE TRIGGER IF NOT EXISTS evidence_no_update BEFORE UPDATE ON evidence_objects
 BEGIN SELECT RAISE(ABORT,'evidence bytes immutable'); END;
CREATE TRIGGER IF NOT EXISTS evidence_no_delete BEFORE DELETE ON evidence_objects
 BEGIN SELECT RAISE(ABORT,'retain historical evidence'); END;
CREATE TRIGGER IF NOT EXISTS best_no_update BEFORE UPDATE ON best_events
 BEGIN SELECT RAISE(ABORT,'best-run history is append-only'); END;
CREATE TRIGGER IF NOT EXISTS best_no_delete BEFORE DELETE ON best_events
 BEGIN SELECT RAISE(ABORT,'best-run history is append-only'); END;
CREATE TRIGGER IF NOT EXISTS snapshot_no_update BEFORE UPDATE ON snapshots
 BEGIN SELECT RAISE(ABORT,'snapshots are append-only'); END;
CREATE TRIGGER IF NOT EXISTS snapshot_no_delete BEFORE DELETE ON snapshots
 BEGIN SELECT RAISE(ABORT,'snapshots are append-only'); END;
"""


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def record_study(root: Path, database: Path | None = None) -> str:
    """Append research evidence without promoting a family candidate to champion."""
    root = root.resolve()
    database = database or root.parent/'funded-research-state.sqlite3'
    definition = (root/'protocol.json').read_bytes()
    summary = (root/'summary.json').read_bytes()
    identity = sha(str(root).encode())
    with sqlite3.connect(database) as db:
        db.execute('PRAGMA foreign_keys=ON')
        db.executescript(DDL)
        old = db.execute('SELECT protocol_sha256 FROM campaigns WHERE id=?', (identity,)).fetchone()
        if old and old[0] != sha(definition):
            raise ValueError('Study definition changed')
        db.execute('INSERT OR IGNORE INTO campaigns VALUES(?,?,?,?,?)',
                   (identity, str(root), sha(definition), definition.decode(), time.time_ns()))
        for path in sorted(root.rglob('*.json')):
            raw = path.read_bytes()
            db.execute('INSERT OR IGNORE INTO evidence_objects VALUES(?,?)', (sha(raw), raw))
        db.execute('INSERT OR IGNORE INTO snapshots VALUES(?,?,?,?,?)',
                   (sha((identity+sha(summary)).encode()), identity, summary.decode(), sha(summary), time.time_ns()))
    return identity


def sync_campaign(root: Path, database: Path | None = None) -> dict:
    root=root.resolve()
    database=database or root.parent/'funded-research-state.sqlite3'
    database.parent.mkdir(parents=True,exist_ok=True)
    definition=(root/'campaign.json').read_bytes()
    protocol=json.loads(definition)
    campaign_id=sha(str(root).encode())
    with sqlite3.connect(database) as db:
        db.execute('PRAGMA foreign_keys=ON')
        db.executescript(DDL)
        old=db.execute('SELECT protocol_sha256 FROM campaigns WHERE id=?',(campaign_id,)).fetchone()
        if old and old[0]!=sha(definition): raise ValueError('Campaign definition changed')
        db.execute('INSERT OR IGNORE INTO campaigns VALUES(?,?,?,?,?)',
                   (campaign_id,str(root),sha(definition),definition.decode(),time.time_ns()))
        summary=json.loads((root/'summary.json').read_text()) if (root/'summary.json').exists() else None
        progress=json.loads((root/'progress.json').read_text()) if (root/'progress.json').exists() else []
        events=[]
        if summary:
            events.append((0,summary['initial_training']['policy'],summary['initial_training']['score'],root/'summary.json'))
        previous=float('-inf') if not summary else summary['initial_training']['score']
        for item in progress:
            if item['incumbent_score']>previous:
                events.append((item['batch'],item['incumbent'],item['incumbent_score'],root/f'batch-{item["batch"]:02d}'/'results.json'))
                previous=item['incumbent_score']
        for batch,policy,score,evidence in events:
            raw=evidence.read_bytes()
            db.execute('INSERT OR IGNORE INTO evidence_objects VALUES(?,?)',(sha(raw),raw))
            policy_json=json.dumps(policy,sort_keys=True,separators=(',',':'))
            identity=sha(f'{campaign_id}:{batch}'.encode())
            existing=db.execute('SELECT policy_json,training_score FROM best_events WHERE id=?',(identity,)).fetchone()
            if existing and existing!=(policy_json,score): raise ValueError('Best-run evidence was rewritten')
            db.execute('INSERT OR IGNORE INTO best_events VALUES(?,?,?,?,?,?,?,?,?)',
                       (identity,campaign_id,batch,sha(policy_json.encode()),policy_json,score,str(evidence),sha(evidence.read_bytes()),time.time_ns()))
        if summary:
            content=json.dumps(summary,sort_keys=True,separators=(',',':'))
            snapshot_id=sha((campaign_id+content).encode())
            db.execute('INSERT OR IGNORE INTO snapshots VALUES(?,?,?,?,?)',
                       (snapshot_id,campaign_id,content,sha((root/'summary.json').read_bytes()),time.time_ns()))
        rows=db.execute('SELECT batch,policy_json,training_score,evidence_path,evidence_sha256 FROM best_events WHERE campaign_id=? ORDER BY batch',(campaign_id,)).fetchall()
        snapshot=db.execute('SELECT data_json FROM snapshots WHERE campaign_id=? ORDER BY recorded_at_ns DESC LIMIT 1',(campaign_id,)).fetchone()
    payload={'campaign_id':campaign_id,'database':str(database.resolve()),'protocol':protocol,
             'best_history':[{'batch':r[0],'policy':json.loads(r[1]),'training_score':r[2],'evidence_path':r[3],'evidence_sha256':r[4]} for r in rows],
             'summary':json.loads(snapshot[0]) if snapshot else None,'batches':progress,
             'gemini_diagnostic':json.loads((root/'gemini-diagnostic.json').read_text()) if (root/'gemini-diagnostic.json').exists() else None}
    (root/'workflow-state.json').write_text(json.dumps(payload,indent=2)+'\n')
    return payload


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--campaign',type=Path,required=True)
    p.add_argument('--visual-output',type=Path)
    a=p.parse_args()
    data=sync_campaign(a.campaign)
    if a.visual_output:
        template=Path(__file__).resolve().parents[2]/'schemas/funded-workflow-template.html'
        encoded=json.dumps(data,allow_nan=False).replace('</','<\\/')
        a.visual_output.parent.mkdir(parents=True,exist_ok=True)
        a.visual_output.write_text(template.read_text().replace('__DATA__',encoded))
    print(json.dumps({'saved_bests':len(data['best_history']),'database':data['database']}))
