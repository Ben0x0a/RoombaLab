"""Operator settings, read once from the environment (.env via docker compose).

Used by: roombalab.db (DB_PATH), roombalab.robot (ROOMBA_*, CALL_TIMEOUT_SECONDS,
RECONNECT_SECONDS), roombalab.server (PORT, PUBLIC_DIR, *_HOURS), roombalab.snapshot (SNAPSHOT_DIR),
roombalab.retention (DISK_THRESHOLD_PCT, RETENTION_*, ARCHIVE_DIR, WATCHED_DISKS).
Uses: nothing inside the package.
"""

import os
from pathlib import Path

ROOMBA_IP = os.environ.get("ROOMBA_IP", "")
ROOMBA_BLID = os.environ.get("ROOMBA_BLID", "")
ROOMBA_PASSWORD = os.environ.get("ROOMBA_PASSWORD", "")
PLACEHOLDER_VALUE = "CHANGE_ME"

PORT = int(os.environ.get("PORT", "8080"))
# Live database: on a Docker volume, opened by this container only (see docker-compose.yml).
DB_PATH = Path(os.environ.get("DB_PATH", "/db/roombalab.sqlite"))
# Read-only copies for the Mac, in the folder shared with it. Used by roombalab.snapshot.
SNAPSHOT_DIR = Path(os.environ.get("SNAPSHOT_DIR", "/data/snapshots"))
SNAPSHOT_HOURS = max(1.0, float(os.environ.get("SNAPSHOT_HOURS", "24")))
# Static front end shipped next to the package in the image (/app/public).
PUBLIC_DIR = Path(os.environ.get("PUBLIC_DIR", Path(__file__).resolve().parents[2] / "public"))

# Upper bound for one outbound call (command, schedule write).
CALL_TIMEOUT_SECONDS = max(1.0, float(os.environ.get("CALL_TIMEOUT_SECONDS", "15")))
# Wait between attempts while the FIRST connection cannot be established.
# roombapy only supervises reconnection once a first session exists.
RECONNECT_SECONDS = max(5.0, float(os.environ.get("RECONNECT_SECONDS", "30")))

# Retention (roombalab.retention): when any watched disk is at or above DISK_THRESHOLD_PCT, the oldest raw
# data is archived to ARCHIVE_DIR then deleted, but never data younger than RETENTION_MIN_DAYS.
DISK_THRESHOLD_PCT = min(99.0, max(10.0, float(os.environ.get("DISK_THRESHOLD_PCT", "75"))))
RETENTION_MIN_DAYS = max(1.0, float(os.environ.get("RETENTION_MIN_DAYS", "90")))
RETENTION_CHECK_HOURS = max(0.25, float(os.environ.get("RETENTION_CHECK_HOURS", "6")))
ARCHIVE_DIR = Path(os.environ.get("ARCHIVE_DIR", "/data/archive"))
# Label -> a path on each disk to watch: Docker's disk (database volume) and the Mac's disk (shared folder).
# A full Mac disk also stops Docker's disk image from growing, so both matter.
WATCHED_DISKS = {"Docker disk (database)": DB_PATH.parent, "Mac disk (shared folder)": SNAPSHOT_DIR.parent}


def robot_configured() -> bool:
    """True when every credential is set and none is still the .env.example placeholder."""
    values = (ROOMBA_IP, ROOMBA_BLID, ROOMBA_PASSWORD)
    return all(values) and PLACEHOLDER_VALUE not in values
