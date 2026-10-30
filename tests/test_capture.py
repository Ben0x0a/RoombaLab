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
                  SNAPSHOT_DIR=str(_TMP / "snapshots"), ARCHIVE_DIR=str(_TMP / "archive"))

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
    assert "usage" in api.get("/api/storage").json()
    assert len(api.get("/api/export/raw.jsonl").text.splitlines()) == db.execute("SELECT COUNT(*) FROM raw_messages").fetchone()[0]


def test_runs_are_segmented_from_mission_status_with_outcome_and_csv(client):
    import csv
    import io

    from fastapi.testclient import TestClient

    from roombalab import runs
    from roombalab.server import app

    shadow = lambda body: client._handle_message(SHADOW_TOPIC_NAME, ('{"state":{"reported":%s}}' % body).encode())  # noqa: E731
    shadow('{"bbmssn":{"nMssn":152,"nMssnOk":40,"nMssnC":96,"nMssnF":16}}')
    shadow('{"cleanMissionStatus":{"cycle":"none","phase":"charge"}}')
    shadow('{"cleanMissionStatus":{"cycle":"clean","phase":"run","nMssn":0,"initiator":"localApp","mssnStrtTm":1793380609}}')
    shadow('{"cleanMissionStatus":{"cycle":"clean","phase":"run","nMssn":153}}')
    for x, y, th in ((0, 0, 0), (30, 40, 10), (30, 100, 90)):  # poses arrive on "wifistat", like the 960 sends them
        client._handle_message("wifistat", ('{"state":{"reported":{"pose":{"theta":%d,"point":{"x":%d,"y":%d}}}}}' % (th, x, y)).encode())
    run = runs.segment()[-1]
    assert (run["derived"]["outcome"], run["derived"]["positions"], run["derived"]["path_length_reported_units"]) == ("ongoing", 3, 110)
    assert (run["measured"]["nMssn"], run["measured"]["initiator"]) == (153, "localApp")

    shadow('{"cleanMissionStatus":{"cycle":"none","phase":"charge"}}')
    shadow('{"bbmssn":{"nMssn":153,"nMssnOk":41,"nMssnC":96,"nMssnF":16}}')
    run = runs.segment()[-1]
    assert run["end"] is not None and run["derived"]["outcome"] == "completed" and run["phases"] == ["run", "charge"]

    api = TestClient(app)
    resp = api.get(f"/api/runs/{run['id']}/export.csv")
    assert resp.status_code == 200 and "attachment" in resp.headers["content-disposition"]
    lines = list(csv.DictReader(io.StringIO(resp.text)))
    assert len(lines) == 3 and (lines[-1]["x_reported"], lines[-1]["theta_reported_deg"], lines[-1]["step_reported_units_derived"]) == ("30", "90", "60.0")
    assert api.get("/api/runs/run-0/positions").status_code == 404

    # Sequence observed on the real 960: Stop (cycle none, phase stop, cancelled counter +1), then Dock with the
    # SAME run number -> one run with a return-to-dock leg, not two runs.
    shadow('{"cleanMissionStatus":{"cycle":"clean","phase":"run","nMssn":154,"initiator":"localApp"}}')
    client._handle_message("wifistat", b'{"state":{"reported":{"pose":{"theta":0,"point":{"x":0,"y":0}}}}}')
    shadow('{"cleanMissionStatus":{"cycle":"none","phase":"stop","nMssn":154}}')
    shadow('{"bbmssn":{"nMssn":154,"nMssnOk":41,"nMssnC":97,"nMssnF":16}}')
    shadow('{"cleanMissionStatus":{"cycle":"dock","phase":"hmUsrDock","nMssn":154}}')
    client._handle_message("wifistat", b'{"state":{"reported":{"pose":{"theta":180,"point":{"x":-50,"y":0}}}}}')
    run = runs.segment()[-1]
    assert run["measured"]["nMssn"] == 154 and run["derived"]["outcome"] == "cancelled"
    assert run["return_to_dock"] is not None and run["return_to_dock"]["end"] is None  # still driving home
    assert run["derived"]["positions"] == 2 and run["derived"]["return_leg_positions"] == 1  # the return leg's pose is part of the run
    shadow('{"cleanMissionStatus":{"cycle":"none","phase":"charge","nMssn":154}}')
    run = runs.segment()[-1]
    assert run["return_to_dock"]["end"] is not None and run["return_to_dock"]["phases"] == ["hmUsrDock", "charge"]
    assert sum(r["measured"].get("nMssn") == 154 for r in runs.segment()) == 1


def test_download_db_is_a_consistent_standalone_copy(client):
    import sqlite3

    from fastapi.testclient import TestClient

    from roombalab.server import app

    resp = TestClient(app).get("/api/export/db.sqlite")
    assert resp.status_code == 200 and resp.content[:16] == b"SQLite format 3\x00"
    copy = _TMP / "downloaded.sqlite"
    copy.write_bytes(resp.content)
    ro = sqlite3.connect(copy)
    assert ro.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert ro.execute("SELECT COUNT(*) FROM raw_messages").fetchone()[0] == db.execute("SELECT COUNT(*) FROM raw_messages").fetchone()[0]
    ro.close()


def test_storage_history_reports_size_daily_volume_and_a_labelled_growth_estimate(client, monkeypatch):
    from roombalab import retention

    monkeypatch.setattr(retention, "disk_usage", lambda: {"disk": {"total_bytes": 10**9, "free_bytes": 5 * 10**8, "used_pct": 50.0}})
    retention.sample_storage()
    retention.sample_storage()
    h = retention.storage_history()
    assert h["db_bytes"] > 0 and h["raw_rows"] == db.execute("SELECT COUNT(*) FROM raw_messages").fetchone()[0]
    assert len(h["samples"]) >= 2 and h["daily"] and h["daily"][-1]["messages"] > 0
    # Samples span less than a day here, so the growth must say it is an estimate from raw data.
    assert h["growth"]["method"].startswith("estimate") and h["growth"]["bytes_per_day"] > 0
    room = 5 * 10**8 - 10**9 * (1 - 0.75)  # free bytes before the 75 % threshold
    assert h["days_to_threshold"] == max(0, round(room / h["growth"]["bytes_per_day"]))


# Retention tests delete rows, so they run last.
def test_retention_deletes_old_wifi_signal_first_archives_it_and_keeps_the_chain_verifiable(client, monkeypatch):
    import gzip

    from roombalab import integrity, retention

    for i in range(5):
        client._handle_message("wifistat", b'{"state":{"reported":{"signal":{"rssi":-40,"snr":%d}}}}' % i)
        client._handle_message(SHADOW_TOPIC_NAME, b'{"state":{"reported":{"batPct":%d}}}' % (90 + i))
    # The 960 also sends its pose on the "wifistat" topic during a run: it must NOT count as Wi-Fi signal.
    client._handle_message("wifistat", b'{"state":{"reported":{"pose":{"theta":5,"point":{"x":1,"y":2}}}}}')
    old_poses = db.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
    db.execute("UPDATE raw_messages SET received_at='2020-01-01T00:00:00.000Z'")  # everything so far is "old"
    client._handle_message("wifistat", b'{"state":{"reported":{"signal":{"rssi":-41,"snr":9}}}}')  # recent: must stay
    from roombalab.retention import SIGNAL_ONLY_SQL

    old_wifi = db.execute(f"SELECT COUNT(*) FROM raw_messages WHERE {SIGNAL_ONLY_SQL} AND received_at < '2021'").fetchone()[0]
    shadow_before = db.execute("SELECT COUNT(*) FROM raw_messages WHERE operation LIKE '%shadow%'").fetchone()[0]

    calls = iter([{"disk": {"total_bytes": 1000, "free_bytes": 100, "used_pct": 90.0}},
                  {"disk": {"total_bytes": 1000, "free_bytes": 600, "used_pct": 40.0}}])
    monkeypatch.setattr(retention, "disk_usage", lambda: next(calls))
    result = retention.check_and_prune()

    assert result["deleted_rows"] == old_wifi > 0
    assert db.execute(f"SELECT COUNT(*) FROM raw_messages WHERE {SIGNAL_ONLY_SQL}").fetchone()[0] == 1  # the recent one
    assert db.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == old_poses  # trajectories untouched
    assert db.execute("SELECT COUNT(*) FROM raw_messages WHERE operation LIKE '%shadow%'").fetchone()[0] == shadow_before
    assert not db.execute("SELECT COUNT(*) FROM observations o LEFT JOIN raw_messages r ON r.id=o.raw_message_id WHERE r.id IS NULL").fetchone()[0]
    archive = _TMP / "archive" / result["archives"][0]
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == (archive.parent / (archive.name + ".sha256")).read_text().split()[0]
    assert len(gzip.open(archive, "rt").read().splitlines()) == old_wifi
    v = integrity.verify_chain()
    assert v["ok"] and v["recorded_gaps"] > 0

    # A row deleted WITHOUT a retention record is reported.
    victim = db.execute("SELECT seq FROM raw_messages WHERE operation LIKE '%shadow%' ORDER BY seq DESC LIMIT 1 OFFSET 1").fetchone()[0]
    db.execute("DELETE FROM raw_messages WHERE seq=?", (victim,))
    v = integrity.verify_chain()
    assert not v["ok"] and "not recorded" in v["reason"]


def test_retention_never_deletes_data_younger_than_the_minimum(client, monkeypatch):
    from roombalab import retention

    db.execute("UPDATE raw_messages SET received_at=?", (retention.now_iso(),))
    monkeypatch.setattr(retention, "disk_usage", lambda: {"disk": {"total_bytes": 1000, "free_bytes": 10, "used_pct": 99.0}})
    result = retention.check_and_prune()
    assert result["deleted_rows"] == 0 and "Still above" in result["message"]
