# Setup and operations

Run commands from the repository root. Use the existing checkout; cloud tasks
are isolated and do not need a Git worktree. Python 3.12+ and Node.js 22+ are
prerequisites. Cloud Python/Node versions used: 3.12.14 / 24.19.0.

```bash
export UV_CACHE_DIR=/tmp/kalshi-uv-cache
export UV_LINK_MODE=copy
uv sync --frozen --extra dev
npm --cache /tmp/kalshi-npm-cache ci --ignore-scripts
npm run build
.venv/bin/python -m pytest -q
```

Caches use writable locations because this cloud machine's home is read-only.
`uv.lock` and `package-lock.json` pin installs; `requirements.lock` is the
hash-verified pip export for Windows. To update it intentionally after a future
dependency change:

```bash
uv export --frozen --extra dev --no-emit-project --format requirements-txt --output-file requirements.lock
```

Windows setup uses `pip install --require-hashes -r requirements.lock`; running
from the repository root makes the backend importable without editable install.
The provided Windows launcher starts one backend, waits for HTTP liveness, opens
the dashboard, and owns shutdown of that child process. It does not install
tools or change firewall settings. Windows execution still needs platform testing.

Start the cloud backend with:

```bash
.venv/bin/python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

Validate `/api/health`, dashboard HTML and compiled JS, and `/api/stream` locally.
Health is process liveness; per-feed readiness is in `/api/state`. The onboarding
UI has no supported loopback preview. Do not present loopback URLs as cloud previews.
Stop/restart the process when changing credentials; processes do not survive
environment publication/restoration.

## Network and credentials

Required hosts: `api.elections.kalshi.com`, `external-api-ws.kalshi.com`,
`ws.kraken.com`; documentation: `docs.kalshi.com`, `docs.kraken.com`; optional
connectivity diagnostics: `api.kraken.com`. Standard package-manager hosts are
already provided by the cloud preset. Keep TLS, artifact hashes, and signatures enabled.

REST and Kraken require no secrets. Complete streaming acceptance needs
`KALSHI_API_KEY_ID` and `KALSHI_PRIVATE_KEY_PATH`. The latter names a securely
provided local PEM file outside Git; its contents must be available for local
signing. A network-proxy credential placeholder cannot perform this signing.
Use supported secure provisioning, never chat, tracked files, or plaintext
values in saved environment scripts. No keys were present during validation.

Run `.venv/bin/python scripts/live_check.py --seconds 30` after credentials and
network settings are applied. Success requires actual data, recognized market
rules, and current CF references for all assets. Missing required data exits 2.
It does not silently replace required streams with fixtures or REST snapshots.

## Phase 2 paper runtime

The backend opens `data/paper.sqlite3` on startup and retains it across restarts.
It starts the manual paper-order worker and read-only official settlement reconciler.
Automatic strategy entries and real-money execution remain disabled. The dashboard
includes manual paper controls and portfolio/order state. A buy without current
CF and venue data is rejected without changing cash or positions.

Defaults are validated from `config/paper.json`; `PAPER_CONFIG_PATH` and
`PAPER_DATABASE_PATH` optionally select local files. Do not delete an existing DB
or overwrite balances during setup. Existing balances override the new-account
starting balance. Existing account precision cannot silently change. Stop the
backend before file-copy backups, or use SQLite's online backup API.

Local startup readiness now includes `GET /api/paper/portfolio`: it must report
`PAPER ONLY`, persisted cash/positions, and current order state. `/api/health`
reports phase 2, `paper_api_available=true`, and `real_order_execution_enabled=false`.
Paper accounting tests use isolated databases and do not reset the actual portfolio.
