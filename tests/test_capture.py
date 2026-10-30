"""Raw-capture tests driven through the production RawCaptureClient, parser and API, with no robot.

Uses: roombalab.robot, roombalab.db, roombalab.server (via FastAPI's TestClient).
"""

import asyncio
import hashlib
import os
import tempfile
from pathlib import Path

# Configuration is read at import time, so the environment is set before importing roombalab.
_TMP = Path(tempfile.mkdtemp())
os.environ.update(DB_PATH=str(_TMP / "test.sqlite"), ROOMBA_IP="10.0.0.1", ROOMBA_BLID="blid-secret",
                  ROOMBA_PASSWORD="pw-secret", CALL_TIMEOUT_SECONDS="1", PUBLIC_DIR=str(_TMP),
                  SNAPSHOT_DIR=str(_TMP / "snapshots"))

import pytest  # noqa: E402
from roombapy import RoombaConnectionError  # noqa: E402

from roombalab import robot  # noqa: E402
from roombalab.db import db, rows  # noqa: E402

SHADOW_TOPIC_NAME = "$aws/things/blid-secret/shadow/update"
POSE_PAYLOAD = b'{"state":{"reported":{"pose":{"theta":12,"point":{"x":3,"y":-4}},"batPct":87,"cleanSchedule":{"cycle":["none"]}}}}'


@pytest.fixture(scope="module")
def client():
    c = robot.RawCaptureClient("10.0.0.1", "blid-secret", "pw-secret")
    robot.robot = c
    return c


def _last_raw():
    return rows("SELECT * FROM raw_messages ORDER BY seq DESC LIMIT 1")[0]


def test_valid_payload_is_journalled_parsed_and_still_reaches_roombapy(client):
    client._handle_message(SHADOW_TOPIC_NAME, POSE_PAYLOAD)
    raw = _last_raw()
    assert raw["operation"] == f"mqtt:{SHADOW_TOPIC_NAME}"
    assert raw["raw_payload"].encode() == POSE_PAYLOAD
    assert raw["parse_status"] == "ok"
    pos = rows("SELECT * FROM positions WHERE raw_message_id=?", (raw["id"],))[0]
    assert (pos["x"], pos["y"], pos["theta"], pos["x_path"]) == (3, -4, 12, "state.reported.pose.point.x")
    # super()._handle_message ran: roombapy's own state was updated from the same message.
    assert client.master_state["state"]["reported"]["batPct"] == 87


def test_malformed_payload_is_kept_as_parse_error(client):
    client._handle_message("wifistat", b"not json {")
    raw = _last_raw()
    assert (raw["raw_payload"], raw["parse_status"]) == ("not json {", "error")
    assert rows("SELECT * FROM parser_errors WHERE raw_message_id=?", (raw["id"],))


def test_non_utf8_payload_is_kept_escaped(client):
    client._handle_message("wifistat", b"\xff{")
    raw = _last_raw()
    assert (raw["raw_payload"], raw["parse_status"]) == ("\\xff{", "error")


def test_hash_chain_verifies_end_to_end():
    prev = None
    for r in rows("SELECT * FROM raw_messages ORDER BY seq"):
        assert r["raw_sha256"] == hashlib.sha256(r["raw_payload"].encode()).hexdigest()
        expected = hashlib.sha256(
            f"{prev or ''}|{r['seq']}|{r['source']}|{r['operation'] or ''}|{r['raw_payload']}".encode()
        ).hexdigest()
        assert (r["previous_chain_sha256"], r["chain_sha256"]) == (prev, expected)
        prev = r["chain_sha256"]


def test_status_never_exposes_credentials(client):
    assert robot.status() == {"connected": False, "ip": "10.0.0.1"}


def test_command_while_disconnected_is_logged_as_failed(client):
    with pytest.raises(RoombaConnectionError):
        asyncio.run(robot.send_command("dock"))
    cmd = rows("SELECT * FROM commands ORDER BY id DESC LIMIT 1")[0]
    assert (cmd["command"], cmd["ok"]) == ("dock", 0)
    assert "Not connected" in cmd["error"]


def test_schedule_is_read_from_reported_state_and_journalled_as_cache(client):
    assert robot.get_schedule() == {"cycle": ["none"]}
    raw = _last_raw()
    assert (raw["operation"], raw["transport"]) == ("cache:cleanSchedule", robot.CACHE_TRANSPORT_NAME)


def test_snapshot_is_a_single_readable_copy_with_matching_hash(client):
    import sqlite3

    from roombalab import snapshot

    snapshot.take_snapshot()
    summary = snapshot.take_snapshot()  # second run replaces, never accumulates
    snap_dir = _TMP / "snapshots"
    assert sorted(p.name for p in snap_dir.iterdir()) == ["roombalab-latest.sqlite", "roombalab-latest.sqlite.sha256"]
    copy = snap_dir / "roombalab-latest.sqlite"
    assert hashlib.sha256(copy.read_bytes()).hexdigest() == summary["sha256"]
    ro = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
    assert ro.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    assert ro.execute("SELECT COUNT(*) FROM raw_messages").fetchone()[0] == db.execute("SELECT COUNT(*) FROM raw_messages").fetchone()[0]
    ro.close()


def test_robot_state_keeps_latest_robot_values_with_their_raw_message(client):
    from roombalab import labels

    client._handle_message(SHADOW_TOPIC_NAME, b'{"state":{"reported":{"batPct":42,"cleanMissionStatus":{"phase":"stuck","error":5}}}}')
    raw_id = _last_raw()["id"]
    st = labels.robot_state()
    assert (st["values"]["batPct"]["value"], st["values"]["batPct"]["raw_message_id"]) == (42, raw_id)
    assert st["phase"]["label"] == "Stuck" and "ROOMBA_STATES" in st["phase"]["source"]
    assert st["error"]["label"] == "Right wheel stuck"
    # Connection events are journalled but are not robot state.
    assert not any(k in st["values"] for k in ("event", "ip"))


def test_verify_reports_the_first_tampered_row():
    from roombalab import integrity

    assert integrity.verify_chain()["ok"]
    original = db.execute("SELECT raw_payload FROM raw_messages WHERE seq=2").fetchone()[0]
    db.execute("UPDATE raw_messages SET raw_payload='tampered' WHERE seq=2")  # exactly what TP 3 asks students to do
    try:
        r = integrity.verify_chain()
        assert (r["ok"], r["break_at_seq"], r["checked"]) == (False, 2, 1)
    finally:
        db.execute("UPDATE raw_messages SET raw_payload=? WHERE seq=2", (original,))
    assert integrity.verify_chain()["ok"]


def test_api_routes_answer_like_0_1(client):
    from fastapi.testclient import TestClient

    from roombalab.server import app

    api = TestClient(app)  # no `with`: lifespan (the real connection loop) is not started
    assert api.get("/api/status").json() == {"connected": False, "ip": "10.0.0.1"}
    assert api.post("/api/control/explode").json() == {"error": "unknown command"}
    assert "error" in api.post("/api/control/dock").json()
    assert api.post("/api/db-snapshot").json()["file"] == "roombalab-latest.sqlite"
    assert len(api.get("/api/export/raw.jsonl").text.splitlines()) == db.execute("SELECT COUNT(*) FROM raw_messages").fetchone()[0]
