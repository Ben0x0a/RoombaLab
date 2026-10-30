"""SQLite data store: schema, append-only raw journal and its SHA-256 hash chain.

Used by: roombalab.parser, roombalab.robot, roombalab.server.
Uses: roombalab.config (DB_PATH).

The schema and the chain formula are identical to RoombaLab 0.1 (Node), so a database
created by 0.1 keeps verifying: chain = sha256(prev_chain | seq | source | operation | raw).
"""

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from roombalab import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_messages(
 id INTEGER PRIMARY KEY, seq INTEGER UNIQUE NOT NULL, received_at TEXT NOT NULL,
 source TEXT NOT NULL, operation TEXT, transport TEXT, raw_payload TEXT NOT NULL,
 raw_sha256 TEXT NOT NULL, previous_chain_sha256 TEXT, chain_sha256 TEXT NOT NULL,
 parse_status TEXT NOT NULL DEFAULT 'pending', parse_error TEXT
);
CREATE TABLE IF NOT EXISTS parsed_messages(
 id INTEGER PRIMARY KEY, raw_message_id INTEGER NOT NULL, parser_version TEXT NOT NULL,
 parsed_at TEXT NOT NULL, parsed_json TEXT NOT NULL,
 FOREIGN KEY(raw_message_id) REFERENCES raw_messages(id)
);
CREATE TABLE IF NOT EXISTS observations(
 id INTEGER PRIMARY KEY, raw_message_id INTEGER NOT NULL, observed_at TEXT NOT NULL,
 path TEXT NOT NULL, value_json TEXT, value_type TEXT, interpretation TEXT,
 FOREIGN KEY(raw_message_id) REFERENCES raw_messages(id)
);
CREATE TABLE IF NOT EXISTS positions(
 id INTEGER PRIMARY KEY, raw_message_id INTEGER NOT NULL, observed_at TEXT NOT NULL,
 x REAL, y REAL, theta REAL, x_path TEXT, y_path TEXT, theta_path TEXT,
 FOREIGN KEY(raw_message_id) REFERENCES raw_messages(id)
);
CREATE TABLE IF NOT EXISTS commands(
 id INTEGER PRIMARY KEY, issued_at TEXT NOT NULL, command TEXT NOT NULL,
 request_json TEXT, result_raw_message_id INTEGER, ok INTEGER, error TEXT
);
CREATE TABLE IF NOT EXISTS parser_errors(
 id INTEGER PRIMARY KEY, raw_message_id INTEGER, occurred_at TEXT NOT NULL,
 stage TEXT NOT NULL, error TEXT NOT NULL
);
-- Operational, not robot data: database size sampled by roombalab.retention, for the growth chart.
CREATE TABLE IF NOT EXISTS storage_samples(
 id INTEGER PRIMARY KEY, sampled_at TEXT NOT NULL, db_bytes INTEGER NOT NULL,
 raw_rows INTEGER NOT NULL, usage_json TEXT
);
-- Derived (rebuildable): latest robot-reported value per path, so the UI does not scan all observations.
CREATE TABLE IF NOT EXISTS latest_observations(
 path TEXT PRIMARY KEY, raw_message_id INTEGER NOT NULL, observed_at TEXT NOT NULL,
 value_json TEXT, value_type TEXT, interpretation TEXT,
 FOREIGN KEY(raw_message_id) REFERENCES raw_messages(id)
);
CREATE INDEX IF NOT EXISTS idx_raw_time ON raw_messages(received_at);
CREATE INDEX IF NOT EXISTS idx_obs_path ON observations(path);
CREATE INDEX IF NOT EXISTS idx_pos_time ON positions(observed_at);
-- Lookups and deletions by source message (transformation view, retention).
CREATE INDEX IF NOT EXISTS idx_obs_raw ON observations(raw_message_id);
CREATE INDEX IF NOT EXISTS idx_parsed_raw ON parsed_messages(raw_message_id);
CREATE INDEX IF NOT EXISTS idx_pos_raw ON positions(raw_message_id);
"""
# Only values the robot itself reported are "latest state"; connection events and roombapy cache
# copies (cache:*) are journalled too but use other paths. Used by roombalab.parser.
ROBOT_STATE_PREFIX = "state.reported."

BACKFILL_LATEST = f"""
INSERT INTO latest_observations(path,raw_message_id,observed_at,value_json,value_type,interpretation)
SELECT path,raw_message_id,observed_at,value_json,value_type,interpretation FROM observations
WHERE id IN (SELECT MAX(id) FROM observations WHERE path LIKE '{ROBOT_STATE_PREFIX}%' GROUP BY path)
"""


def connect(path=None) -> sqlite3.Connection:
    """Open (and create if needed) the database. One connection, used from the event loop only.

    check_same_thread=False: the module is imported on one thread and the event loop may run on another
    (test client, some ASGI servers). Access stays serialised because every caller runs on that loop.
    """
    conn = sqlite3.connect(path or config.DB_PATH, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    # Databases created before latest_observations existed: rebuild it once from observations.
    if conn.execute("SELECT NOT EXISTS(SELECT 1 FROM latest_observations)").fetchone()[0]:
        conn.execute(BACKFILL_LATEST)
    return conn


db = connect()


def now_iso() -> str:
    """UTC timestamp in the same form as JavaScript's toISOString(), used across the schema."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def to_raw_text(payload: Any) -> str:
    """Text stored as raw_payload: strings verbatim, anything else as compact JSON (as JSON.stringify did)."""
    if isinstance(payload, str):
        return payload
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def append_raw(source: str, operation: str | None, payload: Any, transport: str = "mqtt/tls") -> int:
    """Append one raw row and extend the hash chain; returns the row id.

    HOW: read the last (seq, chain) pair, hash the payload, then hash the previous chain value together
    with seq/source/operation/payload. WHY inside one transaction: two writers reading the same "last
    row" would fork the chain; BEGIN IMMEDIATE takes the write lock before the read.
    """
    raw = to_raw_text(payload)
    db.execute("BEGIN IMMEDIATE")
    try:
        prev = db.execute("SELECT seq, chain_sha256 FROM raw_messages ORDER BY seq DESC LIMIT 1").fetchone()
        prev_chain = prev["chain_sha256"] if prev else None
        seq = (prev["seq"] if prev else 0) + 1
        chain = sha(f"{prev_chain or ''}|{seq}|{source}|{operation or ''}|{raw}")
        cur = db.execute(
            """INSERT INTO raw_messages
               (seq,received_at,source,operation,transport,raw_payload,raw_sha256,previous_chain_sha256,chain_sha256)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (seq, now_iso(), source, operation, transport, raw, sha(raw), prev_chain, chain),
        )
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise
    return int(cur.lastrowid)


def rows(sql: str, args: tuple = ()) -> list[dict]:
    return [dict(r) for r in db.execute(sql, args).fetchall()]
