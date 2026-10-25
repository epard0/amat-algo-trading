"""Stable economic event traversal without materializing the account event log.

Sort in SQLite with a small page cache and file-backed temporary storage. Funding
is ordered by its economic timestamp, including late publications, before fills
at the same time; rowid breaks ties exactly as the original Python replay did.
No account rows, schema or deduplication identities are changed.
"""

import json


def ordered_events(db, *, liquidations=False):
    db.execute("PRAGMA temp_store=FILE")
    db.execute("PRAGMA cache_size=-2048")
    predicate = "id GLOB 'order:*' OR id GLOB 'funding:*'"
    if liquidations:
        predicate += " OR (id GLOB 'snapshot:*' AND json_array_length(json_extract(result,'$.liquidations'))>0)"
    query = (
        "SELECT rowid FROM events WHERE "
        + predicate
        + " ORDER BY json_extract(body,'$.timestamp_ms'), CASE WHEN id GLOB 'funding:*' THEN 0 ELSE 1 END,rowid"
    )
    # Only small ordering keys/rowids enter the SQLite sorter. Fetch the large
    # JSON payload after ordering so temporary storage does not scale with it.
    for (sequence,) in db.execute(query):
        key, body, result = db.execute(
            "SELECT id,body,result FROM events WHERE rowid=?", (sequence,)
        ).fetchone()
        payload = json.loads(body)
        when = payload["timestamp_ms"]
        if type(when) is not int:
            raise ValueError("ledger timestamp must be integer milliseconds")
        yield when, sequence, key, payload, json.loads(result)
