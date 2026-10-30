"""INTERPRETED layer for the dashboard: robot state from latest values, each label with its source.

Used by: roombalab.server (GET /api/state).
Uses: roombapy constants (ROOMBA_STATES, ROOMBA_ERROR_MESSAGES, vendor texts), roombalab.db.

Every label names where it comes from, so a student can tell a measurement from a dictionary lookup.
"""

import json

from roombapy import ROOMBA_ERROR_MESSAGES, ROOMBA_STATES, UNVERIFIED_OVERLAP, vendor_error_text

from roombalab.db import ROBOT_STATE_PREFIX, rows


def latest() -> dict[str, dict]:
    """Latest robot-reported value per path (short path, without the state.reported. prefix)."""
    out = {}
    for r in rows("SELECT * FROM latest_observations ORDER BY path"):
        r["value"] = json.loads(r["value_json"]) if r["value_json"] is not None else None
        out[r["path"].removeprefix(ROBOT_STATE_PREFIX)] = r
    return out


def phase_label(phase) -> dict | None:
    if phase is None:
        return None
    label = ROOMBA_STATES.get(phase)
    return {
        "label": label or f"Unknown phase “{phase}”",
        "source": "roombapy ROOMBA_STATES" if label else "none (value not listed)",
    }


def error_label(code) -> dict | None:
    if not isinstance(code, int) or code == 0:
        return None
    # roombapy documents its vendor texts as coming from iRobot's newer app and unverified on older robots:
    # every code shared with ROOMBA_ERROR_MESSAGES is in UNVERIFIED_OVERLAP, and vendor-only codes were never checked.
    return {
        "code": code,
        "label": ROOMBA_ERROR_MESSAGES.get(code),
        "label_source": "roombapy ROOMBA_ERROR_MESSAGES (long used with 900-series robots)",
        "vendor": vendor_error_text(code, "en"),
        "vendor_source": "iRobot's newer app texts, via roombapy — not verified for the 900 series"
        + (" (wording differs from roombapy's label)" if code in UNVERIFIED_OVERLAP else ""),
    }


def robot_state() -> dict:
    values = latest()
    get = lambda p: (values.get(p) or {}).get("value")  # noqa: E731
    return {
        "values": values,
        "phase": phase_label(get("cleanMissionStatus.phase")),
        "error": error_label(get("cleanMissionStatus.error")),
        "last_message_at": max((v["observed_at"] for v in values.values()), default=None),
    }
