# Kalshi Crypto Trading Terminal V2

Phase 1 read-only market-data terminal for BTC, ETH, and SOL. Python 3.12+,
FastAPI, asyncio, Decimal books, and a small TypeScript dashboard. There is no
order submission or paper execution in this phase. No mock feed is used by the app.

## Run

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

Public Kalshi REST discovery/books/trades and Kraken WebSocket v2 need no keys.
Kalshi WebSocket subscriptions, including CF Benchmarks, require a Kalshi API
key ID and a corresponding private signing key available locally. Configure
`KALSHI_API_KEY_ID` and `KALSHI_PRIVATE_KEY_PATH` in the launching process.
The application does not load `.env` files automatically. Never commit the key,
enter it in chat, send it to the frontend, or use a proxy token placeholder as
a local signing key. Keep its file outside this checkout with restricted access.

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
