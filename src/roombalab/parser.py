"""Reference parser: RAW -> PARSED -> NORMALISED observations / positions -> INTERPRETED labels.

Used by: roombalab.robot (parse_and_store after every append_raw).
Uses: roombalab.db.

Same output as the 0.1 Node parser (paths, value types, labels); a parse failure never deletes the raw
row, it is recorded in raw_messages.parse_status and parser_errors so it can be fixed and replayed.
"""

import json
import math
import re
from typing import Any

from roombalab.db import ROBOT_STATE_PREFIX, db, now_iso

# 0.3.0: interpretation labels translated to English (0.2.0 wrote French labels).
PARSER_VERSION = "0.3.0"

# Pose locations seen in robot payloads, tried in order: (x path, y path, theta path).
POSE_PATHS = (
    ("pose.point.x", "pose.point.y", "pose.theta"),
    ("state.reported.pose.point.x", "state.reported.pose.point.y", "state.reported.pose.theta"),
)

INTERPRETATIONS = (
    (re.compile(r"batPct$", re.I), "Reported battery: {v}%"),
    (re.compile(r"nWStll$", re.I), "Raw wheel-stall counter"),
    (re.compile(r"nMBStll$", re.I), "Raw main-brush stall counter"),
    (re.compile(r"nCliffs", re.I), "Raw cliff-sensor counter"),
    (re.compile(r"nStuck$", re.I), "Raw stuck-event counter"),
    (re.compile(r"phase$", re.I), "Reported phase: {v}"),
)


def flatten(value: Any, prefix: str = "", out: list | None = None) -> list[tuple[str, Any]]:
    """Dotted path -> leaf value. Objects are descended into; arrays are kept whole as one leaf."""
    out = [] if out is None else out
    if isinstance(value, dict):
        for key, child in value.items():
            flatten(child, f"{prefix}.{key}" if prefix else key, out)
    else:
        out.append((prefix, value))
    return out


def value_type(v: Any) -> str:
    """JavaScript-style type names, so rows match those written by RoombaLab 0.1."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, (int, float)):
        return "number"
    if isinstance(v, list):
        return "array"
    return "string" if isinstance(v, str) else type(v).__name__


def _get(obj: Any, path: str) -> Any:
    for key in path.split("."):
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def _number(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) else None


def find_pose(obj: Any) -> dict | None:
    """Return x/y/theta exactly as reported (no unit conversion) plus the paths they came from."""
    for xp, yp, tp in POSE_PATHS:
        x, y = _number(_get(obj, xp)), _number(_get(obj, yp))
        if x is not None and y is not None:
            return {"x": x, "y": y, "theta": _number(_get(obj, tp)), "xp": xp, "yp": yp, "tp": tp}
    return None


def interpret(path: str, v: Any) -> str | None:
    for pattern, label in INTERPRETATIONS:
        if pattern.search(path):
            return label.format(v=v)
    return None


def parse_and_store(raw_id: int, payload: Any) -> None:
    try:
        obj = json.loads(payload) if isinstance(payload, str) else payload
        now = now_iso()
        db.execute("BEGIN")
        db.execute(
            "INSERT INTO parsed_messages(raw_message_id,parser_version,parsed_at,parsed_json) VALUES(?,?,?,?)",
            (raw_id, PARSER_VERSION, now, json.dumps(obj, separators=(",", ":"), ensure_ascii=False)),
        )
        obs = [(raw_id, now, p, json.dumps(v, ensure_ascii=False), value_type(v), interpret(p, v)) for p, v in flatten(obj)]
        db.executemany(
            "INSERT INTO observations(raw_message_id,observed_at,path,value_json,value_type,interpretation) VALUES(?,?,?,?,?,?)",
            obs,
        )
        db.executemany(
            """INSERT INTO latest_observations(raw_message_id,observed_at,path,value_json,value_type,interpretation)
               VALUES(?,?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET raw_message_id=excluded.raw_message_id,
               observed_at=excluded.observed_at, value_json=excluded.value_json, value_type=excluded.value_type,
               interpretation=excluded.interpretation""",
            [o for o in obs if o[2].startswith(ROBOT_STATE_PREFIX)],
        )
        pose = find_pose(obj)
        if pose:
            db.execute(
                "INSERT INTO positions(raw_message_id,observed_at,x,y,theta,x_path,y_path,theta_path) VALUES(?,?,?,?,?,?,?,?)",
                (raw_id, now, pose["x"], pose["y"], pose["theta"], pose["xp"], pose["yp"], pose["tp"]),
            )
        db.execute("UPDATE raw_messages SET parse_status='ok' WHERE id=?", (raw_id,))
        db.execute("COMMIT")
    except Exception as e:
        if db.in_transaction:
            db.execute("ROLLBACK")
        error = f"{type(e).__name__}: {e}"
        db.execute("UPDATE raw_messages SET parse_status='error', parse_error=? WHERE id=?", (error, raw_id))
        db.execute(
            "INSERT INTO parser_errors(raw_message_id,occurred_at,stage,error) VALUES(?,?,?,?)",
            (raw_id, now_iso(), "parse", error),
        )
