"""Cleaning runs (missions): segmentation from raw observations, per-run positions and CSV export.

Used by: roombalab.server (GET /api/runs, /api/runs/{id}/positions, /api/runs/{id}/export.csv).
Uses: roombalab.db.

Nothing here is stored: runs are DERIVED on demand from the observations, so changing the rules and
RUN_RULES_VERSION re-segments all past data. Rules (calibrated on a real Roomba 960 run, firmware 2.4.17):
- a segment starts when cleanMissionStatus.cycle leaves "none" and ends when it returns to "none"
  (no end yet = ongoing); a segment whose cycle is "dock" is a docking, any other cycle is a cleaning;
- v2: a docking that follows a cleaning and carries the same run number (nMssn) is that run's return to the
  dock — observed: Stop → cycle "none" (phase "stop"), then Dock → cycle "dock" (phase "hmUsrDock"), nMssn unchanged;
- positions are the pose messages received from the start to the end of the return leg (the 960 sends them
  ~1/s on the topic named "wifistat", separately from the mission status);
- outcome: which bbmssn lifetime counter (nMssnOk / nMssnC / nMssnF) rose between the last value seen before
  the cleaning and the first value seen after the cleaning ended (observed: nMssnC 96 -> 97 after a Stop).
"""

import csv
import io
import json
import math

from roombalab.db import rows

RUN_RULES_VERSION = "2"
MISSION_PREFIX = "state.reported.cleanMissionStatus."
COUNTERS_PREFIX = "state.reported.bbmssn."
OUTCOME_COUNTERS = {"nMssnOk": "completed", "nMssnC": "cancelled", "nMssnF": "failed"}
DOCK_CYCLE_NAME = "dock"


def _messages() -> list[dict]:
    """Mission-status and mission-counter values grouped per raw message, in seq order."""
    out: list[dict] = []
    for o in rows(
        """SELECT o.path, o.value_json, r.id AS raw_id, r.seq, r.received_at
           FROM observations o JOIN raw_messages r ON r.id = o.raw_message_id
           WHERE o.path LIKE ? OR o.path LIKE ? ORDER BY r.seq, o.id""",
        (MISSION_PREFIX + "%", COUNTERS_PREFIX + "%"),
    ):
        if not out or out[-1]["seq"] != o["seq"]:
            out.append({"raw_id": o["raw_id"], "seq": o["seq"], "received_at": o["received_at"], "status": {}, "counters": {}})
        value = json.loads(o["value_json"]) if o["value_json"] is not None else None
        if o["path"].startswith(COUNTERS_PREFIX):
            out[-1]["counters"][o["path"].removeprefix(COUNTERS_PREFIX)] = value
        else:
            out[-1]["status"][o["path"].removeprefix(MISSION_PREFIX)] = value
    return out


def _segments() -> list[dict]:
    segments: list[dict] = []
    current = None
    counters: dict = {}
    for msg in _messages():
        point = {"raw_id": msg["raw_id"], "seq": msg["seq"], "received_at": msg["received_at"]}
        if msg["counters"]:
            counters = {**counters, **msg["counters"]}
            for seg in segments:  # first counters after a segment ended decide its outcome
                if seg["end"] and seg["_counters_after"] is None:
                    seg["_counters_after"] = dict(counters)
        st = msg["status"]
        cycle = st.get("cycle")
        if current is None and cycle not in (None, "none"):
            current = {"id": f"run-{msg['seq']}", "kind": "docking" if cycle == DOCK_CYCLE_NAME else "cleaning",
                       "start": point, "end": None, "return_to_dock": None, "measured": {"cycle": cycle},
                       "phases": [], "errors": [], "_counters_before": dict(counters), "_counters_after": None}
            segments.append(current)
        if current is not None:
            _update_measured(current, st)
            if cycle == "none":
                current["end"] = point
                current = None
    return segments


def _update_measured(seg: dict, st: dict) -> None:
    m = seg["measured"]
    phase = st.get("phase")
    if phase and (not seg["phases"] or seg["phases"][-1] != phase):
        seg["phases"].append(phase)
    if st.get("nMssn"):
        m["nMssn"] = st["nMssn"]
    if st.get("mssnStrtTm") and "mssnStrtTm" not in m:
        m["mssnStrtTm"] = st["mssnStrtTm"]
    if st.get("initiator") and "initiator" not in m:
        m["initiator"] = st["initiator"]
    for field in ("mssnM", "sqft"):
        if isinstance(st.get(field), (int, float)):
            m[field] = max(m.get(field, 0), st[field])
    if st.get("error") and st["error"] not in seg["errors"]:
        seg["errors"].append(st["error"])


def segment() -> list[dict]:
    """All runs, oldest first. `measured` comes from the robot; `derived` holds RoombaLab's computations."""
    runs: list[dict] = []
    for seg in _segments():
        prev = runs[-1] if runs else None
        if (seg["kind"] == "docking" and prev and prev["kind"] == "cleaning" and prev["end"] and prev["return_to_dock"] is None
                and seg["measured"].get("nMssn") and seg["measured"].get("nMssn") == prev["measured"].get("nMssn")):
            prev["return_to_dock"] = {"start": seg["start"], "end": seg["end"], "phases": seg["phases"]}
            continue
        runs.append(seg)
    for run in runs:
        run["derived"] = _derive(run)
        del run["_counters_before"], run["_counters_after"]
    return runs


def _derive(run: dict) -> dict:
    before, after = run["_counters_before"], run["_counters_after"]
    if run["end"] is None:
        outcome = "ongoing"
    elif run["kind"] == "docking":
        outcome = "docking only"
    elif not after:
        outcome = "unknown (no mission counters received since the end)"
    else:
        rose = [label for key, label in OUTCOME_COUNTERS.items()
                if isinstance(after.get(key), int) and isinstance(before.get(key), int) and after[key] > before[key]]
        outcome = rose[0] if len(rose) == 1 else "unknown (counters inconclusive)"
    pts = positions(run)
    steps = [math.hypot(b["x"] - a["x"], b["y"] - a["y"]) for a, b in zip(pts, pts[1:])]
    leg = run.get("return_to_dock")
    # Observed on the 960: no pose at all is sent while it drives back to the dock, so this is often 0.
    leg_points = sum(1 for q in pts if q["received_at"] >= leg["start"]["received_at"]) if leg else None
    return {"rules_version": RUN_RULES_VERSION, "outcome": outcome, "positions": len(pts), "return_leg_positions": leg_points,
            "path_length_reported_units": round(sum(steps)), "first_pose_at": pts[0]["received_at"] if pts else None,
            "last_pose_at": pts[-1]["received_at"] if pts else None}


def positions(run: dict) -> list[dict]:
    """Pose messages received from the start to the end of the return leg (until now when ongoing), oldest first."""
    leg = run.get("return_to_dock")
    last = leg["end"] if leg else run["end"]
    end = last["received_at"] if last else "9999"
    return rows(
        """SELECT p.x, p.y, p.theta, p.x_path, p.raw_message_id, r.seq, r.received_at, r.raw_sha256
           FROM positions p JOIN raw_messages r ON r.id = p.raw_message_id
           WHERE r.received_at >= ? AND r.received_at <= ? ORDER BY r.seq""",
        (run["start"]["received_at"], end),
    )


def find(run_id: str) -> dict | None:
    return next((r for r in segment() if r["id"] == run_id), None)


def to_csv(run: dict) -> str:
    """One line per position. Coordinates are written exactly as reported: their unit is not confirmed."""
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["run_id", "rules_version", "seq", "raw_message_id", "raw_sha256", "received_at_utc",
                "x_reported", "y_reported", "theta_reported_deg", "step_reported_units_derived"])
    prev = None
    for p in positions(run):
        step = "" if prev is None else round(math.hypot(p["x"] - prev["x"], p["y"] - prev["y"]), 3)
        w.writerow([run["id"], RUN_RULES_VERSION, p["seq"], p["raw_message_id"], p["raw_sha256"], p["received_at"],
                    _num(p["x"]), _num(p["y"]), _num(p["theta"]), step])
        prev = p
    return out.getvalue()


def _num(v):
    """Write integral floats as integers (the robot reports integers; SQLite REAL returns 12.0)."""
    return int(v) if isinstance(v, float) and v.is_integer() else v
