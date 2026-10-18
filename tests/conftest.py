import pytest


@pytest.fixture
def market_raw():
    # Synthetic protocol fixture, not a captured/live market or contract rulebook.
    return {
        "ticker": "TEST-BTC-ROUND",
        "event_ticker": "TEST-BTC",
        "title": "Bitcoin 15-minute test",
        "status": "active",
        "open_time": "2026-10-18T12:00:00Z",
        "close_time": "2026-10-18T12:15:00Z",
        "yes_sub_title": "Up",
        "no_sub_title": "Down",
        "strike_type": "greater_or_equal",
        "floor_strike": "100000.25",
        "rules_primary": "If the simple average of the sixty seconds of CF Benchmarks' BRTI before 12:15 UTC on Oct 18, 2026 is at least the simple average of the sixty seconds of CF Benchmarks' BRTI before 12:00 UTC on October 18, 2026, then the market resolves to Yes.",
        "rules_secondary": "Tie resolves YES. Synthetic fixture only.",
    }
