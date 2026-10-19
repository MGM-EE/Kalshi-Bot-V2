import asyncio
import random
import time
from email.utils import parsedate_to_datetime


def error_label(exc):
    """Do not echo request headers, PEM paths, credentials, or remote response bodies."""
    status = getattr(getattr(exc, "response", None), "status_code", None)
    return f"HTTP {status}" if status else type(exc).__name__


def retry_delay(failures, exc):
    """Quick recovery after a transient drop; bounded retries during an outage."""
    delay = min(30.0, 0.5 * 2 ** min(max(0, failures - 1), 6))
    delay = min(30.0, random.uniform(0.8, 1.2) * delay)
    requested = getattr(exc, "retry_after", 0)
    response = getattr(exc, "response", None)
    if response is not None:
        value = response.headers.get("Retry-After")
        if value:
            try:
                requested = max(float(requested), float(value))
            except (TypeError, ValueError):
                try:
                    requested = max(
                        float(requested), parsedate_to_datetime(value).timestamp() - time.time()
                    )
                except (TypeError, ValueError, OverflowError):
                    pass
    try:
        return max(delay, min(86400.0, max(0.0, float(requested))))
    except (TypeError, ValueError):
        return delay


async def retry(state, source, operation, invalidate=lambda: None):
    failures = 0
    while True:
        try:
            started = asyncio.get_running_loop().time()
            await operation()
            failures = 0
            # A normal session return (for example waiting for discovery) must
            # yield even when an adapter returns without performing any I/O.
            await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            invalidate()
            # Stable sessions reset backoff; failed handshakes increase it.
            if asyncio.get_running_loop().time() - started > 60:
                failures = 0
            failures += 1
            delay = retry_delay(failures, exc)
            state.health(
                source, "disconnected",
                f"{error_label(exc)}; retrying in {delay:.1f}s (attempt {failures})",
            )
            await asyncio.sleep(delay)
