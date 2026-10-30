"""Operator settings, read once from the environment (.env via docker compose).

Used by: roombalab.db (DB_PATH), roombalab.robot (ROOMBA_*, CALL_TIMEOUT_SECONDS,
RECONNECT_SECONDS), roombalab.server (PORT, PUBLIC_DIR).
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


def robot_configured() -> bool:
    """True when every credential is set and none is still the .env.example placeholder."""
    values = (ROOMBA_IP, ROOMBA_BLID, ROOMBA_PASSWORD)
    return all(values) and PLACEHOLDER_VALUE not in values
