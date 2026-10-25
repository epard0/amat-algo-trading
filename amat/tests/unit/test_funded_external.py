import csv
import datetime as dt
import hashlib
import json

import pytest

from autoresearch.funded_external import load


def fixture(root):
    files = []
    for kind, offset in [('Last', 0), ('Bid', -.25), ('Ask', .25)]:
        path = root/f'20250602.{kind}.csv'
        with path.open('w', newline='') as output:
            writer = csv.writer(output)
            writer.writerow(['datetime', 'open', 'high', 'low', 'close', 'volume'])
            for minute in range(390):
                stamp = dt.datetime(2025, 6, 2, 9, 31)+dt.timedelta(minutes=minute)
                writer.writerow([stamp.isoformat(), 20000+offset, 20001+offset, 19999+offset, 20000+offset, 100])
        files.append({'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    (root/'manifest.json').write_text(json.dumps({'files': files}))


def test_end_stamped_minutes_align_last_and_side_quotes(tmp_path):
    fixture(tmp_path)
    sessions, quotes = load(tmp_path)
    assert len(sessions) == 1
    assert sessions[0][1].shape == (390, 5)
    assert quotes['2025-06-02']['bid'][0, 0] == 19999.75
    assert quotes['2025-06-02']['ask'][0, 0] == 20000.25
    # Without the timestamp shift the 09:30 observation is absent; abstain.
    sessions, quotes = load(tmp_path, 0)
    assert sessions[0][1] is None
    assert not quotes


def test_external_data_tampering_rejected(tmp_path):
    fixture(tmp_path)
    with (tmp_path/'20250602.Last.csv').open('a') as output:
        output.write('changed')
    with pytest.raises(ValueError, match='hash changed'):
        load(tmp_path)
