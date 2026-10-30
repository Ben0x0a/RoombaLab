"""Single connection manager to the Roomba (roombapy, local MQTT/TLS).

Used by: roombalab.server.
Uses: roombapy (pinned ==2.0.2, see RawCaptureClient), roombalab.config, roombalab.db, roombalab.parser.

Firmware 2.x/3.x accepts one local client only, so every module goes through this file.
"""

import asyncio
import json
import logging

from roombapy import RoombaAuthError, RoombaClient

from roombalab import config
from roombalab.db import append_raw, db, now_iso
from roombalab.parser import parse_and_store

_LOG = logging.getLogger(__name__)

# Commands exposed by the web UI; the value is the command string sent to the robot.
COMMANDS = {"clean": "clean", "pause": "pause", "resume": "resume", "stop": "stop", "dock": "dock"}
CACHE_TRANSPORT_NAME = "roombapy-cache"


def record(operation: str, payload, transport: str = "mqtt/tls") -> int:
    raw_id = append_raw("roomba", operation, payload, transport)
    parse_and_store(raw_id, payload)
    return raw_id


class RawCaptureClient(RoombaClient):
    """RoombaClient that journals every MQTT payload byte-for-byte before roombapy decodes it.

    WHY a private-method override: roombapy only hands callbacks the decoded dict and silently drops
    payloads that are not a JSON object, so the public callbacks can neither see the exact bytes nor
    the malformed messages. _handle_message(topic, raw_payload) is the one place that receives both.
    It is private API, hence roombapy is pinned to an exact version and tests/test_capture.py fails
    if the hook stops being called.
    """

    def _handle_message(self, topic: str, raw_payload: bytes) -> None:
        try:
            # A non-UTF-8 payload is kept with its bytes escaped (\xNN) and then fails parsing visibly.
            record(f"mqtt:{topic}", raw_payload.decode("utf-8", errors="backslashreplace"))
        except Exception:
            # Losing raw data must be loud, but must not kill roombapy's read loop (that would drop the session).
            _LOG.exception(f"Could not journal MQTT payload on {topic}")
        super()._handle_message(topic, raw_payload)


# Guard against a roombapy upgrade renaming the hook: without it the override is silently never called.
assert hasattr(RoombaClient, "_handle_message"), "roombapy no longer has _handle_message; raw capture would be lost"

robot: RawCaptureClient | None = None


def _on_connection_state(state: str, error: str | None) -> None:
    payload = {"event": state, "ip": config.ROOMBA_IP} if error is None else {"event": state, "error": error}
    record("connection", payload, "local")


async def run() -> None:
    """Keep trying to open the first session; after that roombapy reconnects on its own with backoff.

    Stops for good on RoombaAuthError: a wrong BLID/password does not become right by retrying.
    """
    global robot
    if not config.robot_configured():
        _LOG.warning("Roomba credentials not set in .env; collector idle")
        return
    robot = RawCaptureClient(config.ROOMBA_IP, config.ROOMBA_BLID, config.ROOMBA_PASSWORD)
    robot.register_on_connection_state_callback(_on_connection_state)
    while True:
        try:
            await robot.connect()
            return
        except RoombaAuthError as e:
            record("connection_error", {"error": f"auth: {e}"}, "local")
            _LOG.error(f"Roomba rejected the credentials: {e}")
            return
        except Exception as e:
            record("connection_error", {"error": str(e)}, "local")
            _LOG.warning(f"Roomba unreachable ({e}); retrying in {config.RECONNECT_SECONDS:.0f}s")
            await asyncio.sleep(config.RECONNECT_SECONDS)


async def stop() -> None:
    if robot is not None:
        await robot.disconnect()


def status() -> dict:
    # BLID and password are secrets (dossier §16): never sent to the browser.
    return {"connected": bool(robot and robot.connected), "ip": config.ROOMBA_IP}


def _require_robot() -> RawCaptureClient:
    if robot is None:
        raise RuntimeError("Roomba not configured")
    return robot


async def _logged_call(command: str, request, coro_factory) -> dict:
    """Run one outbound action with a timeout and journal it in `commands`.

    The result only means roombapy published the MQTT message; whether the robot acted on it is shown
    by its subsequent state messages, which are captured as raw data.
    """
    issued = now_iso()
    try:
        await asyncio.wait_for(coro_factory(), timeout=config.CALL_TIMEOUT_SECONDS)
    except Exception as e:
        error = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
        db.execute(
            "INSERT INTO commands(issued_at,command,request_json,ok,error) VALUES(?,?,?,0,?)",
            (issued, command, json.dumps(request), error),
        )
        raise
    db.execute(
        "INSERT INTO commands(issued_at,command,request_json,ok) VALUES(?,?,?,1)",
        (issued, command, json.dumps(request)),
    )
    return {"published": True}


async def send_command(name: str) -> dict:
    client = _require_robot()
    return await _logged_call(name, [], lambda: client.send_command(COMMANDS[name]))


def snapshot() -> dict:
    """Journal and return roombapy's accumulated reported state.

    Tagged transport=roombapy-cache: it merges earlier messages and is NOT a new robot message.
    """
    reported = _require_robot().master_state.get("state", {}).get("reported", {})
    record("cache:reported_state", reported, CACHE_TRANSPORT_NAME)
    return reported


def get_schedule():
    schedule = _require_robot().master_state.get("state", {}).get("reported", {}).get("cleanSchedule")
    if schedule is None:
        raise LookupError("cleanSchedule not reported by the robot yet")
    record("cache:cleanSchedule", schedule, CACHE_TRANSPORT_NAME)
    return schedule


async def set_schedule(schedule: dict) -> dict:
    # Same delta message dorita980's setWeek sends: {"state": {"cleanSchedule": ...}}.
    client = _require_robot()
    return await _logged_call("setSchedule", schedule, lambda: client.set_preferences({"cleanSchedule": schedule}))
