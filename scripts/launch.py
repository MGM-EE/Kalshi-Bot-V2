"""Local launcher; Ctrl+C terminates only the backend started by this process."""

import signal
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
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
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


if __name__ == "__main__":
    # Let the finally block own shutdown on platforms delivering SIGTERM.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    main()
