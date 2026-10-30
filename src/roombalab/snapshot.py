"""Consistent copies of the live database into the folder shared with the Mac.

Used by: roombalab.server (startup, periodic task, POST /api/db-snapshot).
Uses: roombalab.config (SNAPSHOT_*), roombalab.db (live connection).

WHY copies: the live database stays on a Docker volume that only this container opens. Opening it
from macOS through Docker Desktop's shared folder while the container writes can corrupt it (SQLite's
WAL locking does not cross the VM boundary). A copy is written once, then never touched again by the
container, so any tool on the Mac can read it safely.
"""

import hashlib
import logging
import sqlite3
from pathlib import Path

from roombalab import config
from roombalab.db import db, now_iso

_LOG = logging.getLogger(__name__)
LATEST_NAME = "roombalab-latest.sqlite"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_copy(dest: Path) -> str:
    """Write a consistent copy at `dest` and its .sha256 sidecar; returns the hash.

    HOW: SQLite's online backup API copies a single consistent state while writers continue, into a
    temporary file that is switched to rollback-journal mode (a plain single file, no -wal/-shm needed
    to read it), then renamed into place so a reader never sees a half-written copy.
    """
    tmp = dest.with_name(dest.name + ".tmp")
    tmp.unlink(missing_ok=True)
    target = sqlite3.connect(tmp)
    try:
        db.backup(target)
        target.execute("PRAGMA journal_mode = DELETE")
    finally:
        target.close()
    digest = _sha256_file(tmp)
    tmp.replace(dest)
    sidecar = dest.with_name(dest.name + ".sha256")
    sidecar_tmp = sidecar.with_name(sidecar.name + ".tmp")
    sidecar_tmp.write_text(f"{digest}  {dest.name}\n", encoding="utf-8")
    sidecar_tmp.replace(sidecar)
    return digest


def take_snapshot() -> dict:
    """Replace roombalab-latest.sqlite with a fresh copy (only one copy is kept); return a summary."""
    config.SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    taken_at = now_iso()
    digest = _write_copy(config.SNAPSHOT_DIR / LATEST_NAME)
    _LOG.info(f"Database snapshot written: {LATEST_NAME} ({digest[:16]}…)")
    return {"file": LATEST_NAME, "taken_at": taken_at, "sha256": digest}
