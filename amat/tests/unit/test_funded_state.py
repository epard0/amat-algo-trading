import json
import sqlite3

import pytest

from autoresearch.funded_state import record_study, sync_campaign


def setup(root):
    root.mkdir()
    (root/'campaign.json').write_text(json.dumps({'target':3000}))
    (root/'summary.json').write_text(json.dumps({'initial_training':{'policy':{'risk':50},'score':0}}))
    (root/'progress.json').write_text('[]')


def add(root,batch,score):
    history=json.loads((root/'progress.json').read_text())
    history.append({'batch':batch,'incumbent_score':score,'incumbent':{'risk':100}})
    folder=root/f'batch-{batch:02d}';folder.mkdir()
    (folder/'results.json').write_text(json.dumps(history[-1]))
    (root/'progress.json').write_text(json.dumps(history))


def test_previous_bests_retained_and_sync_idempotent(tmp_path):
    root=tmp_path/'campaign';setup(root);add(root,1,10)
    assert len(sync_campaign(root)['best_history'])==2
    assert len(sync_campaign(root)['best_history'])==2
    add(root,2,15)
    data=sync_campaign(root)
    assert [x['training_score'] for x in data['best_history']]==[0,10,15]
    with sqlite3.connect(data['database']) as db:
        with pytest.raises(sqlite3.IntegrityError,match='append-only'):
            db.execute('DELETE FROM best_events')
        assert db.execute('SELECT count(*) FROM evidence_objects').fetchone()[0]==3


def test_changed_campaign_or_best_evidence_rejected(tmp_path):
    root=tmp_path/'campaign';setup(root);add(root,1,10);sync_campaign(root)
    history=json.loads((root/'progress.json').read_text());history[0]['incumbent_score']=11
    (root/'progress.json').write_text(json.dumps(history))
    with pytest.raises(ValueError,match='rewritten'):
        sync_campaign(root)
    (root/'campaign.json').write_text(json.dumps({'target':100}))
    with pytest.raises(ValueError,match='definition changed'):
        sync_campaign(root)


def test_campaigns_keep_separate_histories(tmp_path):
    a=tmp_path/'a';b=tmp_path/'b';setup(a);setup(b)
    add(a,1,10);add(b,1,20)
    left=sync_campaign(a);right=sync_campaign(b)
    assert left['campaign_id']!=right['campaign_id']
    assert left['best_history'][-1]['training_score']==10
    assert right['best_history'][-1]['training_score']==20


def test_indicator_study_persists_without_promoting_or_replacing_champion(tmp_path):
    old = tmp_path/'old'
    setup(old)
    add(old, 1, 10)
    sync_campaign(old)
    study = tmp_path/'study'
    study.mkdir()
    (study/'protocol.json').write_text('{"families":7}')
    (study/'summary.json').write_text('{"deployment":"DISABLED"}')
    first = record_study(study)
    assert record_study(study) == first
    with sqlite3.connect(tmp_path/'funded-research-state.sqlite3') as db:
        assert db.execute('SELECT count(*) FROM best_events').fetchone()[0] == 2
        assert db.execute('SELECT count(*) FROM snapshots WHERE campaign_id=?', (first,)).fetchone()[0] == 1
    (study/'protocol.json').write_text('{"families":8}')
    with pytest.raises(ValueError, match='definition changed'):
        record_study(study)
