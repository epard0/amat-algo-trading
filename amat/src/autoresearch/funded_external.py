"""Pinned public contract-labelled Last/Bid/Ask minute data, never upstream code."""
import csv
import datetime as dt
import hashlib
import io
import json
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

from autoresearch.funded import load_sessions, write

COMMIT = 'fc5508e2c152938d6d9eb70a36b888ae26107176'
REPO = 'mbytes21/MNQ_DATA'


def fetch(root, tree):
    if root.exists():
        raise ValueError('Use a fresh data directory')
    paths = {}
    for item in tree['tree']:
        name = item['path']
        parts = name.split('/')
        if 'MNQ 06-25' not in parts or item['type'] != 'blob':
            continue
        date = parts[-1].split('.')[0]
        if not ('20250317' <= date <= '20250613') or not name.endswith(('.Last.csv', '.Bid.csv', '.Ask.csv')):
            continue
        if dt.datetime.strptime(date, '%Y%m%d').weekday() >= 5:
            continue
        if parts[-1] not in paths or len(name) < len(paths[parts[-1]]):
            paths[parts[-1]] = name
    plan = {'repo': REPO, 'commit': COMMIT, 'contract_label': 'MNQ 06-25', 'paths': paths,
            'date_range': ['2025-03-17', '2025-06-13'],
            'timezone': 'America/New_York ASSUMED: maintenance gap at17:01–18:00 and volume pattern; not independently verified',
            'timestamp_semantics': 'minute-end ASSUMED; subtract one minute; sensitivity uses unshifted timestamps',
            'quality': 'public_unverified_contract_label; minute quote OHLC, not synchronized event-level BBO',
            'promotion_eligible': False}
    write(root/'download-plan.json', plan)
    def download(item):
        filename, path = item
        url = f'https://raw.githubusercontent.com/{REPO}/{COMMIT}/'+quote(path)
        for attempt in range(3):
            try:
                with urllib.request.urlopen(url, timeout=30) as response:
                    raw = response.read(500001)
                if len(raw) > 500000:
                    raise ValueError('Unexpected file size')
                if not raw.startswith(b'datetime,open,high,low,close,volume'):
                    raise ValueError('Unexpected CSV schema')
                target = root/'raw'/filename
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)
                return {'path': str(target.resolve()), 'source': url, 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}
            except Exception:
                if attempt == 2:
                    raise
    with ThreadPoolExecutor(max_workers=8) as pool:
        files = list(pool.map(download, sorted(paths.items())))
    write(root/'manifest.json', {**plan, 'files': files, 'downloaded_files': len(files)})
    return load(root)


def load(root, shift_minutes=-1):
    manifest = json.loads((root/'manifest.json').read_text())
    converted = {}
    audits = {}
    for kind in ('Last', 'Bid', 'Ask'):
        rows = {}
        duplicates = 0
        for file in manifest['files']:
            path = Path(file['path'])
            if not path.name.endswith(f'.{kind}.csv'):
                continue
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != file['sha256']:
                raise ValueError('External data hash changed')
            for row in csv.DictReader(io.StringIO(raw.decode())):
                stamp = dt.datetime.fromisoformat(row['datetime'])+dt.timedelta(minutes=shift_minutes)
                row['datetime'] = stamp.isoformat()
                if stamp in rows:
                    if row != rows[stamp]:
                        raise ValueError('Conflicting duplicate timestamps')
                    duplicates += 1
                rows[stamp] = row
        target = root/f'{kind.lower()}-shift-{shift_minutes}.csv'
        with target.open('w', newline='') as output:
            writer = csv.DictWriter(output, fieldnames=('datetime', 'open', 'high', 'low', 'close', 'volume'))
            writer.writeheader()
            writer.writerows(rows[key] for key in sorted(rows))
        sessions, audit = load_sessions(target, 'America/New_York')
        converted[kind] = {date: bars for date, bars in sessions if bars is not None}
        audits[kind] = {**audit, 'identical_duplicates_removed': duplicates}
    common = sorted(set(converted['Last']) & set(converted['Bid']) & set(converted['Ask']))
    # Keep calendar dates; missing any series abstains for all matched comparisons.
    sessions = [(date, converted['Last'].get(date) if date in common else None) for date, _ in sessions]
    quotes = {date: {'bid': converted['Bid'][date], 'ask': converted['Ask'][date]} for date in common}
    crossed = sum(int((quotes[d]['bid'][:, 0] > quotes[d]['ask'][:, 0]).sum()) for d in common)
    write(root/f'audit-shift-{shift_minutes}.json', {'series': audits, 'common_complete_sessions': len(common),
          'minute_open_crossed_quotes': crossed,
          'limitation': 'Bid/Ask minute opens are separate first observations; not synchronized quotes. Event-order replay unavailable.'})
    return sessions, quotes
