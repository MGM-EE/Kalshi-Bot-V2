"""Supervise long-lived workers without hiding an unexpected worker exit."""

import asyncio

from .connectors.common import error_label, retry_delay


async def supervise(state, name, operation, status, invalidate=lambda: None):
    failures = 0
    try:
        while True:
            started = asyncio.get_running_loop().time()
            status.update(state="running")
            try:
                state.health(f"worker:{name}", "running", "Background worker running")
                await operation()
                raise RuntimeError("Background worker exited unexpectedly")
            except asyncio.CancelledError:
                raise
            except Exception as operation_error:
                error = operation_error
                # A failure in cleanup or a health observer must not take out
                # the supervisor responsible for recovering that component.
                try:
                    invalidate()
                except Exception as cleanup_error:
                    error = cleanup_error
                if asyncio.get_running_loop().time() - started >= 60:
                    failures = 0
                failures += 1
                status.update(state="restarting", restarts=status.get("restarts", 0) + 1)
                delay = retry_delay(failures, error)
                try:
                    state.health(
                        f"worker:{name}", "disconnected",
                        f"{error_label(error)}; restarting in {delay:.1f}s",
                    )
                except Exception:
                    pass
                await asyncio.sleep(delay)
    finally:
        status.update(state="stopped")
