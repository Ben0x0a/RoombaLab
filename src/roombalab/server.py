"""HTTP API and static web UI (public/). Keeps the 0.1 routes and adds state, verify and counts.

Used by: Dockerfile (python -m roombalab.server).
Uses: fastapi, uvicorn, roombalab.config, .db, .integrity, .labels, .retention, .robot, .runs, .snapshot.

Every endpoint is async so all SQLite access stays on the event-loop thread (one connection, no locking).
"""

import asyncio
import contextlib
import json
import logging
import os
import sqlite3
import tempfile

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask
from fastapi.staticfiles import StaticFiles

from roombalab import config, integrity, labels, retention, robot, runs
from roombalab.db import db, now_iso, rows
from roombalab.snapshot import take_snapshot


_LOG = logging.getLogger(__name__)


async def every(hours: float, job, name: str) -> None:
    """Run `job` at startup, then every `hours`; a failure is logged and retried at the next interval."""
    while True:
        try:
            job()
        except Exception:
            _LOG.exception(f"{name} failed")
        await asyncio.sleep(hours * 3600)


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    tasks = [
        asyncio.create_task(robot.run()),
        asyncio.create_task(every(config.RETENTION_CHECK_HOURS, retention.check_and_prune, "Retention check")),
        asyncio.create_task(every(config.SNAPSHOT_HOURS, take_snapshot, "Database snapshot")),
    ]
    yield
    for task in tasks:
        task.cancel()
    await robot.stop()


app = FastAPI(lifespan=lifespan)


def _error(e: Exception, status: int = 500) -> HTTPException:
    # app.js reads `error` from the body; FastAPI's default key is `detail`, so errors are re-shaped below.
    return HTTPException(status_code=status, detail=f"{type(e).__name__}: {e}")


@app.exception_handler(HTTPException)
async def http_error(_request: Request, exc: HTTPException):
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)


@app.get("/api/status")
async def status():
    return robot.status()


@app.get("/api/state")
async def state():
    return labels.robot_state()


@app.get("/api/verify")
async def verify():
    return integrity.verify_chain()


@app.get("/api/storage")
async def storage():
    return retention.storage_status()


@app.get("/api/storage/history")
async def storage_history():
    return retention.storage_history()


@app.post("/api/retention")
async def run_retention():
    try:
        return retention.check_and_prune()
    except Exception as e:
        raise _error(e) from e


@app.get("/api/raw")
async def raw(limit: int = 200, errors_only: bool = False, operation: str = ""):
    where, args = [], []
    if errors_only:
        where.append("parse_status='error'")
    if operation:
        where.append("operation LIKE ?")
        args.append(f"%{operation}%")
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    return rows(
        "SELECT id,seq,received_at,source,operation,transport,raw_payload,raw_sha256,chain_sha256,parse_status,parse_error "
        f"FROM raw_messages {clause} ORDER BY id DESC LIMIT ?",
        (*args, min(1000, limit)),
    )


@app.get("/api/counts")
async def counts():
    return dict(db.execute(
        "SELECT (SELECT MAX(seq) FROM raw_messages) raw, (SELECT COUNT(*) FROM raw_messages WHERE parse_status='error') parse_errors,"
        " (SELECT COUNT(*) FROM positions) positions, (SELECT COUNT(*) FROM commands) commands"
    ).fetchone())


@app.get("/api/observations")
async def observations(limit: int = 500, skip_signal: bool = False):
    # skip_signal: the robot publishes Wi-Fi RSSI/SNR every few seconds, which buries everything else.
    clause = "WHERE path NOT LIKE 'state.reported.signal.%'" if skip_signal else ""
    return rows(f"SELECT * FROM observations {clause} ORDER BY id DESC LIMIT ?", (min(3000, limit),))


@app.get("/api/positions")
async def positions(limit: int = 20000):
    # The most recent points, returned oldest-first for drawing.
    return rows("SELECT * FROM (SELECT * FROM positions ORDER BY id DESC LIMIT ?) ORDER BY id ASC", (min(20000, limit),))


@app.get("/api/commands")
async def commands():
    return rows("SELECT * FROM commands ORDER BY id DESC LIMIT 500")


@app.get("/api/transform/{raw_id}")
async def transform(raw_id: int):
    row = db.execute("SELECT * FROM raw_messages WHERE id=?", (raw_id,)).fetchone()
    return {
        "raw": dict(row) if row else None,
        "parsed": rows("SELECT * FROM parsed_messages WHERE raw_message_id=?", (raw_id,)),
        "observations": rows("SELECT * FROM observations WHERE raw_message_id=?", (raw_id,)),
        "positions": rows("SELECT * FROM positions WHERE raw_message_id=?", (raw_id,)),
    }


@app.post("/api/snapshot")
async def snapshot():
    try:
        return robot.snapshot()
    except Exception as e:
        raise _error(e) from e


@app.post("/api/control/{cmd}")
async def control(cmd: str):
    if cmd not in robot.COMMANDS:
        raise HTTPException(400, "unknown command")
    try:
        return await robot.send_command(cmd)
    except Exception as e:
        raise _error(e) from e


@app.get("/api/schedule")
async def get_schedule():
    try:
        return robot.get_schedule()
    except LookupError as e:
        raise _error(e, 404) from e
    except Exception as e:
        raise _error(e) from e


@app.post("/api/schedule")
async def set_schedule(request: Request):
    # The robot's native cleanSchedule object, edited as raw JSON in the UI on purpose (teaching).
    try:
        return await robot.set_schedule(await request.json())
    except Exception as e:
        raise _error(e) from e


@app.get("/api/runs")
async def list_runs():
    return list(reversed(runs.segment()))


def _run_or_404(run_id: str) -> dict:
    run = runs.find(run_id)
    if run is None:
        raise HTTPException(404, f"no run {run_id}")
    return run


@app.get("/api/runs/{run_id}/positions")
async def run_positions(run_id: str):
    return runs.positions(_run_or_404(run_id))


@app.get("/api/runs/{run_id}/export.csv")
async def run_csv(run_id: str):
    run = _run_or_404(run_id)
    name = f"roombalab-{run_id}-{run['start']['received_at'][:10]}.csv"
    return Response(runs.to_csv(run), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/api/export/db.sqlite")
async def download_db():
    """A consistent copy of the live database, written to a temporary file and deleted once sent.

    SQLite's backup API copies one consistent state while the collector keeps writing; the copy is
    switched to rollback-journal mode so it is a single self-contained file.
    """
    fd, tmp = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    target = sqlite3.connect(tmp)
    try:
        db.backup(target)
        target.execute("PRAGMA journal_mode = DELETE")
    finally:
        target.close()
    name = f"roombalab-{now_iso().replace(':', '').replace('-', '').split('.')[0]}Z.sqlite"
    return FileResponse(tmp, media_type="application/vnd.sqlite3", filename=name, background=BackgroundTask(os.unlink, tmp))


@app.post("/api/db-snapshot")
async def db_snapshot():
    try:
        return take_snapshot()
    except Exception as e:
        raise _error(e) from e


@app.get("/api/export/raw.jsonl")
async def export_raw():
    async def lines():
        for r in db.execute("SELECT * FROM raw_messages ORDER BY seq"):
            yield json.dumps(dict(r), ensure_ascii=False) + "\n"

    return StreamingResponse(lines(), media_type="application/x-ndjson")


# Mounted last so /api/* routes take precedence over static files.
app.mount("/", StaticFiles(directory=config.PUBLIC_DIR, html=True), name="public")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    uvicorn.run(app, host="0.0.0.0", port=config.PORT)


if __name__ == "__main__":
    main()
