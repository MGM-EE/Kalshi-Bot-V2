# Kalshi Crypto Trading Terminal V2

Live market-data and paper-execution terminal for BTC, ETH, and SOL. Python 3.12+,
FastAPI, asyncio, Decimal books, SQLite accounting, and a small TypeScript dashboard.
Manual controls and optional strategy automation submit paper orders only.
New accounts start paused; enable a selected profile explicitly in the UI.
Saved controls persist across restarts; inspect the selected profile and pause state.
No real-money order transport or mock runtime feed exists.

## Run

**Windows recovery / first-time setup:** follow the [simple Windows guide](docs/windows_quick_start.md).
Extract the download to a permanent folder, open **START_HERE.bat**, connect your
key with **CONNECT_KALSHI.bat**, then open **OPEN_TERMINAL.bat** or the desktop icon.
The dashboard's **What's happening?** panel explains connection, data, paper-bot
and recording status. Do not delete an existing `data` folder when updating.

Windows, with Python 3.12 and Node.js 22+ installed:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/setup_windows.ps1
scripts\start_windows.bat
```

Linux/cloud, with Python 3.12+, Node.js 22+, and uv:

```bash
uv sync --frozen --extra dev
npm ci --ignore-scripts
npm run build
.venv/bin/python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

The Windows launcher opens the local dashboard; close it with Ctrl+C in the
launcher console. Setup must run first. Windows scripts are provided but have
not been executed on Windows in this Linux environment.
If the terminal is already running, opening its launcher again opens the existing
dashboard. Live-update interruptions use a read-only HTTP backup; fresh dashboard
updates and fresh exchange data are reported separately. A browser disconnect
does not pause the backend or change its saved strategy controls.

Public Kalshi REST discovery/books/trades and Kraken WebSocket v2 need no keys.
For Windows-local setup, use [the credential helper](docs/local_credentials.md):
double-click `scripts/configure_kalshi.bat`, enter your API key ID, select the
downloaded private key, and save the Windows-encrypted copy. Restart the app.
The cloud environment cannot access your PC's store.

Kalshi WebSocket subscriptions, including CF Benchmarks, require a Kalshi API
key ID and a corresponding private signing key available locally. Configure
`KALSHI_API_KEY_ID` and `KALSHI_PRIVATE_KEY_PATH` in the launching process.
The application does not load `.env` files automatically. Never commit the key,
enter it in chat, send it to the frontend, or use a proxy token placeholder as
a local signing key. Keep its file outside this checkout with restricted access.

Buys require fresh contract rules, CF reference, and both venues' critical data.
Without authenticated CF access, the terminal displays public data and explains
why buys are rejected. Protective sells need a fresh Kalshi book and current fees;
closed positions wait for official settlement.

Cash, positions, orders, reservations, fees, and fills persist in
`data/paper.sqlite3`. Do not delete it to restart. Defaults are in
[config/paper.json](config/paper.json); optional `PAPER_CONFIG_PATH` and
`PAPER_DATABASE_PATH` select local files. Starting balance applies only to a new
account. Settings saved in SQLite take precedence over initial JSON defaults.
See [paper mechanics](docs/paper_execution.md).
Strategy rules and profile behavior are described in [strategy rules](docs/strategy_rules.md).

The [dashboard guide](docs/dashboard.md) covers condition tiles and editable fields;
[indicator definitions](docs/indicator_definitions.md) document observed scores.
Start a fresh recorded research account with `scripts/start_windows.bat --record
session_name` (or `.venv/bin/python scripts/launch.py --record session_name`). Stop
cleanly, then use Analytics / replay to validate an exact ledger reproduction and
compare profiles. See [replay and analytics](docs/replay_analytics.md) for commands,
period separation, audit exports and limitations.

## Verify

```bash
.venv/bin/python -m pytest -q
npm run typecheck
.venv/bin/python scripts/live_check.py --seconds 30
```

The first two commands are deterministic. The opt-in live check exits **2**
for missing data, incomplete references, or unavailable streaming; a working
REST fallback does not satisfy authenticated streaming acceptance.

See [PROJECT_STATUS.md](PROJECT_STATUS.md) for acceptance status and next steps,
[setup](docs/setup.md) for environment details, and [API sources](docs/api_sources.md)
for verified contracts and remaining live checks.
