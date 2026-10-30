"""Disk-usage check, data-size history and retention of old raw data: archive, delete, record the deletion.

Used by: roombalab.server (periodic task, GET /api/storage, /api/storage/history, POST /api/retention), roombalab.integrity
(RETENTION_* names, to accept recorded gaps).
Uses: roombalab.config, roombalab.db, roombalab.parser.

Rules (chosen by the lab owner):
- trigger when ANY watched disk is at or above DISK_THRESHOLD_PCT;
- delete the oldest Wi-Fi signal messages first (most of the rows, least value), then the oldest of the rest;
- never touch data younger than RETENTION_MIN_DAYS, whatever the disk says;
- archive every deleted raw row to a compressed JSONL file (with SHA-256) before deleting it;
- record each deletion in the raw log itself (operation "retention:prune"), chained like any other message,
  listing the exact deleted seq ranges, so integrity.verify_chain can tell a recorded deletion from a missing row.
"""

import gzip
import hashlib
import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone

from roombalab import config
from roombalab.db import append_raw, db, now_iso, rows
from roombalab.parser import parse_and_store

_LOG = logging.getLogger(__name__)
RETENTION_SOURCE_NAME = "roombalab"
RETENTION_OPERATION_NAME = "retention:prune"
# Deletion tiers, tried in order: (name, SQL condition on raw_messages). Retention records are never deleted:
# verify_chain needs them to accept the gaps they describe.
# The 960 publishes BOTH Wi-Fi signal and, during a run, its pose (1 per second) on the topic named "wifistat",
# so the first tier selects by content: messages whose only reported field is "signal". Matching the topic alone
# would delete the trajectories first.
SIGNAL_ONLY_SQL = (
    "operation = 'mqtt:wifistat' AND json_valid(raw_payload)"
    " AND json_extract(raw_payload, '$.state.reported.signal') IS NOT NULL"
    " AND (SELECT COUNT(*) FROM json_each(raw_payload, '$.state.reported')) = 1"
)
TIERS = (
    ("wifi-signal", SIGNAL_ONLY_SQL),
    ("all", f"NOT (source = '{RETENTION_SOURCE_NAME}' AND operation = '{RETENTION_OPERATION_NAME}')"),
)
BATCH_ROWS = 20000


def disk_usage() -> dict[str, dict]:
    out = {}
    for label, path in config.WATCHED_DISKS.items():
        s = os.statvfs(path)
        total, free = s.f_blocks * s.f_frsize, s.f_bavail * s.f_frsize
        out[label] = {"total_bytes": total, "free_bytes": free, "used_pct": round(100 * (1 - free / total), 1)}
    return out


def bytes_over_threshold(usage: dict) -> int:
    """Bytes to free on the worst disk to get back under the threshold (0 when every disk is under it)."""
    over = [d for d in usage.values() if d["used_pct"] >= config.DISK_THRESHOLD_PCT]
    # At least 1 byte when a disk sits exactly on the threshold, so "at or above" still triggers.
    return max((max(1, int(d["total_bytes"] * (1 - config.DISK_THRESHOLD_PCT / 100) - d["free_bytes"])) for d in over), default=0)


def db_bytes() -> int:
    files = [config.DB_PATH, config.DB_PATH.with_name(config.DB_PATH.name + "-wal")]
    return sum(p.stat().st_size for p in files if p.exists())


def seq_ranges(seqs: list[int]) -> list[list[int]]:
    """[1,2,3,7,8] -> [[1,3],[7,8]]: compact, exact list of deleted seqs."""
    out: list[list[int]] = []
    for s in sorted(seqs):
        if out and s == out[-1][1] + 1:
            out[-1][1] = s
        else:
            out.append([s, s])
    return out


def _archive(batch: list[dict]) -> tuple[str, str]:
    """Write the full raw rows to a gzip JSONL file (temp file, then rename) and its .sha256; return (name, sha)."""
    config.ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = now_iso().replace(":", "").replace("-", "").split(".")[0]
    name = f"roombalab-archive-seq{batch[0]['seq']}-{batch[-1]['seq']}-{stamp}Z.jsonl.gz"
    dest = config.ARCHIVE_DIR / name
    tmp = dest.with_name(name + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        for r in batch:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    digest = hashlib.sha256(tmp.read_bytes()).hexdigest()
    tmp.replace(dest)
    side = dest.with_name(name + ".sha256")
    side_tmp = side.with_name(side.name + ".tmp")
    side_tmp.write_text(f"{digest}  {name}\n", encoding="utf-8")
    side_tmp.replace(side)
    return name, digest


def _delete(ids: list[int]) -> None:
    """Delete raw rows and every derived row that points to them, in one transaction."""
    db.execute("BEGIN IMMEDIATE")
    try:
        db.execute("CREATE TEMP TABLE IF NOT EXISTS prune_ids(id INTEGER PRIMARY KEY)")
        db.execute("DELETE FROM prune_ids")
        db.executemany("INSERT INTO prune_ids(id) VALUES(?)", [(i,) for i in ids])
        for table in ("observations", "positions", "parsed_messages", "latest_observations"):
            db.execute(f"DELETE FROM {table} WHERE raw_message_id IN (SELECT id FROM prune_ids)")
        db.execute("UPDATE commands SET result_raw_message_id=NULL WHERE result_raw_message_id IN (SELECT id FROM prune_ids)")
        db.execute("DELETE FROM raw_messages WHERE id IN (SELECT id FROM prune_ids)")
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise


def check_and_prune() -> dict:
    """Check the disks; if one is over the threshold, archive and delete old raw data until the estimate says enough.

    HOW the amount is chosen: SQLite only returns space to the disk after VACUUM, so the disk cannot be
    re-measured after each batch. The bytes to free are converted to a row count with the database's
    average bytes per raw row, then one VACUUM runs at the end and the disks are measured again.
    """
    usage = disk_usage()
    sample_storage(usage)
    need = bytes_over_threshold(usage)
    result = {"checked_at": now_iso(), "threshold_pct": config.DISK_THRESHOLD_PCT, "usage_before": usage,
              "deleted_rows": 0, "archives": []}
    if need <= 0:
        return {**result, "message": "All disks below the threshold; nothing deleted."}
    cutoff = (datetime.now(timezone.utc) - timedelta(days=config.RETENTION_MIN_DAYS)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    total_rows = db.execute("SELECT COUNT(*) FROM raw_messages").fetchone()[0]
    avg_row_bytes = db_bytes() / max(1, total_rows)
    freed_estimate = 0.0
    for tier, condition in TIERS:
        while freed_estimate < need:
            batch = rows(f"SELECT * FROM raw_messages WHERE {condition} AND received_at < ? ORDER BY seq LIMIT ?", (cutoff, BATCH_ROWS))
            if not batch:
                break
            name, digest = _archive(batch)
            _delete([r["id"] for r in batch])
            record = {
                "tier": tier, "rows": len(batch), "older_than": cutoff, "deleted_seq_ranges": seq_ranges([r["seq"] for r in batch]),
                "archive_file": name, "archive_sha256": digest, "threshold_pct": config.DISK_THRESHOLD_PCT,
                "used_pct_before": {k: v["used_pct"] for k, v in usage.items()},
            }
            raw_id = append_raw(RETENTION_SOURCE_NAME, RETENTION_OPERATION_NAME, record, "local")
            parse_and_store(raw_id, record)
            freed_estimate += avg_row_bytes * len(batch)
            result["deleted_rows"] += len(batch)
            result["archives"].append(name)
            _LOG.warning(f"Retention: archived and deleted {len(batch)} rows ({tier}) to {name}")
    if result["deleted_rows"]:
        try:
            db.execute("VACUUM")
        except sqlite3.OperationalError as e:
            # VACUUM needs free space about the size of the database; deleted pages are still reused for new data.
            _LOG.error(f"Retention: VACUUM failed ({e}); space stays inside the database file")
    after = disk_usage()
    result["usage_after"] = after
    if bytes_over_threshold(after) > 0:
        result["message"] = (
            f"Still above {config.DISK_THRESHOLD_PCT:g}% after deleting {result['deleted_rows']} rows: only data older than "
            f"{config.RETENTION_MIN_DAYS:g} days may be deleted, and the database is {db_bytes() / 1e9:.2f} GB. "
            "The rest of the disk is used by other files."
        )
        _LOG.warning(f"Retention: {result['message']}")
    else:
        result["message"] = f"Deleted {result['deleted_rows']} rows; disks back below the threshold."
    return result


def storage_status() -> dict:
    last = db.execute(
        "SELECT received_at, raw_payload FROM raw_messages WHERE source=? AND operation=? ORDER BY seq DESC LIMIT 1",
        (RETENTION_SOURCE_NAME, RETENTION_OPERATION_NAME),
    ).fetchone()
    return {
        "usage": disk_usage(), "threshold_pct": config.DISK_THRESHOLD_PCT, "min_days": config.RETENTION_MIN_DAYS,
        "db_bytes": db_bytes(), "last_prune": {"at": last["received_at"], **json.loads(last["raw_payload"])} if last else None,
    }


def sample_storage(usage: dict | None = None) -> None:
    """Record the database size now (one row per retention check: at start-up, then every RETENTION_CHECK_HOURS)."""
    usage = usage or disk_usage()
    db.execute(
        "INSERT INTO storage_samples(sampled_at, db_bytes, raw_rows, usage_json) VALUES(?,?,?,?)",
        (now_iso(), db_bytes(), db.execute("SELECT COUNT(*) FROM raw_messages").fetchone()[0], json.dumps(usage)),
    )


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def storage_history() -> dict:
    """Size samples, raw data received per day, and a DERIVED growth rate with the method that produced it.

    Growth is taken from the size samples of the last 7 days when they span at least a day; before that it is
    estimated from the raw data received per day, scaled by the database's bytes per raw-payload byte.
    """
    samples = rows("SELECT sampled_at, db_bytes, raw_rows FROM storage_samples ORDER BY id DESC LIMIT 400")[::-1]
    daily = rows(
        "SELECT substr(received_at,1,10) AS day, COUNT(*) AS messages, SUM(length(raw_payload)) AS payload_bytes "
        "FROM raw_messages GROUP BY day ORDER BY day DESC LIMIT 90"
    )[::-1]
    now_bytes = db_bytes()
    growth = None
    recent = [s for s in samples if _parse_iso(s["sampled_at"]) >= datetime.now(timezone.utc) - timedelta(days=7)]
    if len(recent) >= 2:
        span = (_parse_iso(recent[-1]["sampled_at"]) - _parse_iso(recent[0]["sampled_at"])).total_seconds() / 86400
        if span >= 1:
            growth = {"bytes_per_day": round((recent[-1]["db_bytes"] - recent[0]["db_bytes"]) / span),
                      "method": f"database size samples over the last {span:.1f} days"}
    if growth is None and daily:
        total_payload = db.execute("SELECT SUM(length(raw_payload)) FROM raw_messages").fetchone()[0] or 0
        full_days = daily[1:-1] or daily  # first and last day are usually partial
        per_day = sum(d["payload_bytes"] for d in full_days) / len(full_days)
        ratio = now_bytes / total_payload if total_payload else 0
        growth = {"bytes_per_day": round(per_day * ratio),
                  "method": f"estimate: raw data per day × {ratio:.1f} (database bytes per raw-payload byte); size samples need a day of history"}
    days_to_threshold = None
    if growth and growth["bytes_per_day"] > 0:
        # Free bytes left on each watched disk before it reaches the threshold; the tightest disk decides.
        room = [d["free_bytes"] - d["total_bytes"] * (1 - config.DISK_THRESHOLD_PCT / 100) for d in disk_usage().values()]
        days_to_threshold = max(0, round(min(room) / growth["bytes_per_day"]))
    return {"db_bytes": now_bytes, "raw_rows": db.execute("SELECT COUNT(*) FROM raw_messages").fetchone()[0],
            "samples": samples, "daily": daily, "growth": growth, "days_to_threshold": days_to_threshold,
            "threshold_pct": config.DISK_THRESHOLD_PCT}
