"""Local launcher; Ctrl+C terminates only the backend started by this process."""

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path


def backend_command(platform=None):
    command = [
        sys.executable,
        "-m",
        "uvicorn",
        "backend.app.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        "8000",
    ]
    if (platform or sys.platform) == "win32":
        # Uvicorn 0.54 explicitly creates a Proactor loop on Windows, so merely
        # setting WindowsSelectorEventLoopPolicy in the parent cannot fix its
        # IOCP accept failures (WinError 64). Select the child loop factory.
        command += ["--loop", "asyncio:SelectorEventLoop"]
    return command


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--record",
        help="Start a new isolated recorded paper session (letters, digits, underscore, hyphen)",
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    child_env = os.environ.copy()
    if args.record:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", args.record):
            parser.error("Invalid session name")
        recording = root / "data" / "recordings" / f"{args.record}.jsonl.gz"
        account = root / "data" / "sessions" / f"{args.record}.sqlite3"
        if recording.exists() or account.exists():
            parser.error("Choose a new session name; existing recordings/accounts are preserved")
        child_env.update(RECORDING_PATH=str(recording), PAPER_DATABASE_PATH=str(account))
    if not (root / "frontend/dist/app.js").exists():
        raise SystemExit("Dashboard is not built. On Windows, open START_HERE.bat first.")
    # Do not accidentally launch a second app on an occupied port.
    import socket

    with socket.socket() as check:
        try:
            check.bind(("127.0.0.1", 8000))
        except OSError:
            if not args.record:
                try:
                    with urllib.request.urlopen("http://127.0.0.1:8000/api/health", timeout=2) as r:
                        health = json.load(r)
                    if (
                        isinstance(health, dict)
                        and health.get("application") == "ok"
                        and health.get("paper_api_available") is True
                        and health.get("real_order_execution_enabled") is False
                    ):
                        webbrowser.open("http://127.0.0.1:8000")
                        print("Your terminal is already running. Opened its dashboard.")
                        return
                except (OSError, ValueError):
                    pass
            raise SystemExit(
                "Port 8000 is occupied. Stop the existing application first. "
                "A recorded session needs its own fresh launch."
            ) from None
    command = backend_command()
    if "--loop" in command:
        print("Windows networking compatibility mode is enabled.", flush=True)
    child = subprocess.Popen(
        command,
        cwd=root,
        env=child_env,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
    )
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if child.poll() is not None:
                raise SystemExit("Backend exited during startup")
            try:
                with urllib.request.urlopen("http://127.0.0.1:8000/api/health", timeout=1) as r:
                    if r.status == 200:
                        break
            except OSError:
                time.sleep(0.1)
        else:
            raise SystemExit("Backend readiness timed out")
        webbrowser.open("http://127.0.0.1:8000")
        print("Paper-only terminal running. Press Ctrl+C here to stop.")
        result = child.wait()
        if result:
            raise SystemExit(f"The backend stopped with exit code {result}. See the error above.")
    except KeyboardInterrupt:
        pass
    finally:
        if child.poll() is None:
            child.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT)
            try:
                child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


if __name__ == "__main__":
    # Let the finally block own shutdown on platforms delivering SIGTERM.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    main()
