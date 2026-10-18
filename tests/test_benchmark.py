import json

from backend.app.connectors.benchmark import Benchmark
from backend.app.markets.discovery import normalize_market
from backend.app.state import State


def frame(ts=899000, count=59):
    return {
        "index_id": "BRTI",
        "received_at": ts + 10,
        "data": json.dumps({"type": "value", "id": "BRTI", "time": ts, "value": "100100.25"}),
        "avg_60s_data": {
            "value": "100090.00",
            "window_size": 60,
            "window_start_ts_ms": ts - 60000,
            "window_end_ts_exclusive": ts,
        },
        "last_60s_windowed_average_15min": {
            "value": "100095.00",
            "window_size": count,
            "window_start_ts_ms": 840000,
            "window_end_ts_exclusive": ts,
        },
    }


def test_benchmark_spot_trailing_partial_and_settlement_are_distinct():
    state = State()
    market = {
        "index_id": "BRTI",
        "close_time": 900,
        "averaging_window_seconds": 60,
        "opening_reference": "100000",
        "settlement_value": None,
    }
    state.assets["BTC"]["market"] = market
    state.assets["BTC"]["kraken"]["ticker"] = {"last": "99999"}
    benchmark = Benchmark(state)
    benchmark.handle(frame())
    assert str(market["current_index"]) == "100100.25"
    assert market["opening_reference"] == "100000"
    assert market["trailing_average"]["value"] == "100090.00"
    assert market["partial_final_minute_average"]["window_size"] == 59
    assert market["projected_settlement_average"] is None
    assert market["settlement_value"] is None
    # Duplicate/old frames cannot revive stale data or rewrite current reference.
    row = frame(ts=898000)
    row["data"] = row["data"].replace("100100.25", "99900")
    benchmark.handle(row)
    assert str(market["current_index"]) == "100100.25"


def test_final_average_requires_selected_market_window():
    state = State()
    state.assets["BTC"]["market"] = {
        "index_id": "BRTI",
        "close_time": 1800,
        "averaging_window_seconds": 60,
    }
    Benchmark(state).handle(frame())
    assert state.assets["BTC"]["market"]["partial_final_minute_average"] is None


def test_actual_contract_wording_maps_target_labels_without_using_ticker(market_raw):
    market_raw.update(yes_sub_title="Target Price: $100000.25", no_sub_title="Target price: TBD")
    market = normalize_market(market_raw, {}, "BTC")
    assert market["outcome_mapping"] == {"yes": "UP", "no": "DOWN"}
    assert market["averaging_window_seconds"] == 60
    assert market["index_id"] == "BRTI"


def test_contract_index_alias_and_asset_specific_rounding(market_raw):
    market_raw.update(
        rules_primary=market_raw["rules_primary"].replace("BRTI", "SOLUSDRTI"),
        custom_strike={"round_digits": "4"},
    )
    market = normalize_market(market_raw, {}, "SOL")
    assert market["index_id"] == "SOLUSD_RTI"
    assert market["settlement_rounding_decimal_places"] == "4"
    assert market["outcome_mapping"]["yes"] == "UP"
