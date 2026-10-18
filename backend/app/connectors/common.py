import asyncio
import random


def error_label(exc):
    """Do not echo request headers, PEM paths, credentials, or remote response bodies."""
    status = getattr(getattr(exc, "response", None), "status_code", None)
    return f"HTTP {status}" if status else type(exc).__name__


async def retry(state, source, operation, invalidate=lambda: None):
    failures = 0
    while True:
        try:
            started = asyncio.get_running_loop().time()
            await operation()
            failures = 0
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            invalidate()
            # Stable sessions reset backoff; failed handshakes increase it.
            if asyncio.get_running_loop().time() - started > 60:
                failures = 0
            failures += 1
            state.health(source, "disconnected", error_label(exc))
            await asyncio.sleep(min(30, 2 ** min(failures, 5)) + random.random())
