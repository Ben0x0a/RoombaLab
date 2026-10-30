# RoombaLab

Local telemetry and digital-forensics teaching lab for an iRobot Roomba 960. RoombaLab keeps every
message the robot publishes exactly as received (with SHA-256 and a hash chain), then shows each step
RAW → PARSED → NORMALISED → INTERPRETED in a web interface that also controls the robot.

- Local only: talks to the robot over its local MQTT/TLS interface, no cloud.
- Phone-friendly control panel (clean, pause, resume, stop, dock) and weekly schedule.
- Raw data log with integrity verification, transformation view, diagnostics.
- Trajectory: live view or any recorded cleaning run, with heading (θ), a θ = 0° compass, 30° display
  rotation, and a per-run CSV export.
- One-click download of a consistent copy of the database, plus a daily copy in `data/snapshots`.
- Data size view: database size over time, raw data per day, growth per day and days left before the clean-up threshold.
- One Docker container: Python 3.12, [roombapy](https://github.com/pschmitt/roombapy) 2.0.2, FastAPI, SQLite.

Tested with a Roomba 960 on firmware 2.4.17. The iRobot local API is unofficial; a firmware update can
change or remove fields.

## Requirements

- A Mac (Intel or Apple silicon) with Docker Desktop, on the same network as the robot.
- The robot's IP address, BLID and local password.

## Installation

Full guide, from installing Docker to running 24/7: **[docs/installation.md](docs/installation.md)**.

Short version, once Docker and the network are ready:
```bash
git clone https://github.com/Ben0x0a/RoombaLab.git
cd RoombaLab
cp .env.example .env    # set ROOMBA_IP, ROOMBA_BLID, ROOMBA_PASSWORD
docker compose up -d --build
```
Then open <http://localhost:8080>.

| Setting (`.env`) | Meaning |
|---|---|
| `ROOMBA_IP` / `ROOMBA_BLID` / `ROOMBA_PASSWORD` | Robot address and local credentials (secrets) |
| `HOST_PORT` | Port on the Mac for the web interface (default 8080) |
| `CALL_TIMEOUT_SECONDS` | Max wait for one command or schedule write |
| `RECONNECT_SECONDS` | Wait between attempts while the robot cannot be reached at start-up |
| `SNAPSHOT_HOURS` | Interval between refreshes of the readable database copy |
| `DISK_THRESHOLD_PCT` | Disk usage (Docker's or the Mac's) that triggers the clean-up (default 75) |
| `RETENTION_MIN_DAYS` | Data younger than this is never deleted (default 90) |
| `RETENTION_CHECK_HOURS` | Interval between disk checks (default 6) |

## Data model

- `raw_messages` is append-only: the exact payload of each MQTT message (`mqtt:<topic>`), recorded
  before any decoding, with `raw_sha256` and `chain_sha256 = sha256(previous|seq|source|operation|payload)`.
  A payload that is not JSON (or not UTF-8) is kept and shown as a parse error.
- Derived tables (`parsed_messages`, `observations`, `positions`, `latest_observations`) reference
  `raw_message_id`, record the parser version, and can be rebuilt.
- `cache:*` rows (transport `roombapy-cache`) are roombapy's accumulated state, not new robot messages.
- `commands` only records that a command was published; the robot's following state messages show
  whether it was carried out.
- Coordinates are stored as reported, without unit conversion. θ is in degrees, anticlockwise in the
  x/y frame (checked on a recorded run); the x/y unit is not confirmed yet (documented as mm, looks like cm).
  The 960 sends its pose about once per second during a run, on the MQTT topic named `wifistat`.
- Runs are derived on demand from `cleanMissionStatus.cycle` (start when it leaves `none`, end when it
  returns to it); a following `dock` cycle with the same run number is that run's return to the dock. The
  outcome comes from the `bbmssn` lifetime counters. The rules are versioned in `src/roombalab/runs.py`.
  Observed on firmware 2.4.17: no pose is sent after Stop nor while the robot drives back to the dock.
- Retention: when a disk reaches `DISK_THRESHOLD_PCT`, raw rows older than `RETENTION_MIN_DAYS` are
  archived to `data/archive/*.jsonl.gz` (with SHA-256), then deleted with their derived rows — Wi-Fi signal
  messages first. Each deletion is itself recorded in the log (`retention:prune`, with the exact seq ranges),
  so integrity verification accepts those gaps and still reports any row missing without a record.

The live database stays in a Docker volume; read `data/snapshots/roombalab-latest.sqlite` instead
(see the installation guide, section 11).

Raw capture overrides a private roombapy method (`_handle_message`), so roombapy is pinned to an exact
version and `tests/test_capture.py` fails if capture stops working.

## Tests

```bash
uv sync
uv run pytest
```

## Limits

- Educational project; the integrity chain demonstrates tamper detection, it is not a complete chain of
  custody on its own.
- The robot accepts one local client at a time: close the iRobot app while the lab is connected.
- The web interface has no login; keep it on a trusted network.
- Never publish `.env`: BLID and password are secrets.

## Development & AI use

Generative AI was used in this project mainly to assist during the coding phase. The original ideas and
the overall structure are the owner's, and all core logic has been reviewed. Even so, mistakes or bugs
may have slipped past proof-reading — please report anything unexpected.

## Licence

[MIT](LICENSE). Error texts shown for some fault codes come from iRobot via roombapy and remain
iRobot's.
