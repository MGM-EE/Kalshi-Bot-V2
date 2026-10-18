"""Local launcher; Ctrl+C terminates only the backend started by this process."""

import argparse
import os
import re
import signal
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path


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
        raise SystemExit("UI is not built. Run the documented setup first.")
    # Do not accidentally launch a second app on an occupied port.
    import socket

    with socket.socket() as check:
        try:
            check.bind(("127.0.0.1", 8000))
        except OSError:
            raise SystemExit(
                "Port 8000 is occupied. Stop the existing application first."
            ) from None
    child = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "backend.app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
        ],
        cwd=root,
        env=child_env,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
    )
    try:
        for _ in range(100):
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
        child.wait()
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
