from decimal import Decimal

import pytest

from backend.app.markets.discovery import normalize_market, select_markets, timestamp
from backend.app.markets.orderbook import BookError, KalshiBook, KrakenBook, checksum


def test_explicit_outcomes_and_reference_are_separate(market_raw):
    market = normalize_market(market_raw, {}, "BTC")
    assert market["outcome_mapping"] == {"yes": "UP", "no": "DOWN"}
    assert market["opening_reference"] == "100000.25"
    assert market["current_index"] is None
    assert market["projected_settlement_average"] is None


@pytest.mark.parametrize(
    "patch",
    [
        {"rules_primary": "", "rules_secondary": ""},
        {"rules_primary": "Kraken spot price", "rules_secondary": ""},
        {"strike_type": "between"},
        {"floor_strike": None},
        {"yes_sub_title": "Down", "no_sub_title": "Up"},
        {"close_time": "2026-10-18T13:00:00Z"},
    ],
)
def test_ambiguous_or_conflicting_contract_blocks_mapping(market_raw, patch):
    market = normalize_market({**market_raw, **patch}, {}, "BTC")
    assert market["outcome_mapping"] is None
    assert market["rule_checks"]


def test_ticker_never_determines_outcome(market_raw):
    market_raw.update(
        ticker="LOOKS-LIKE-UP",
        yes_sub_title="Down",
        no_sub_title="Up",
        strike_type="less",
        cap_strike="100000",
        rules_primary=market_raw["rules_primary"].replace("is at least", "is less than"),
    )
    assert normalize_market(market_raw, {}, "BTC")["outcome_mapping"]["yes"] == "DOWN"


def test_rollover_boundaries_and_upcoming(market_raw):
    first = normalize_market(market_raw, {}, "BTC")
    second = {
        **first,
        "ticker": "NEXT",
        "open_time": first["close_time"],
        "close_time": first["close_time"] + 900,
    }
    assert select_markets([first, second], first["close_time"] - 0.01) == (first, [second])
    assert select_markets([first, second], first["close_time"])[0] == second
    assert select_markets([first], first["close_time"])[0] is None
    assert timestamp("2026-10-18T12:15:00Z") == first["close_time"]


def test_kalshi_executable_asks_and_fractional_depth():
    book = KalshiBook()
    book.snapshot([["0.38", "10.50"], ["0.36", "3"]], [["0.60", "4.25"]])
    assert book.view()["yes_bids"][0] == [Decimal("0.38"), Decimal("10.50")]
    assert book.view()["yes_asks"][0] == [Decimal("0.40"), Decimal("4.25")]
    assert book.view()["no_asks"][0][0] == Decimal("0.62")
    book.delta("no", "0.60", "-4.25")
    assert book.view()["yes_asks"] == []


def test_invalid_book_does_not_expose_liquidity():
    book = KalshiBook()
    with pytest.raises(BookError):
        book.delta("yes", "0.4", "1")
    book.snapshot([["0.4", "5"]], [["0.5", "5"]])
    with pytest.raises(BookError):
        book.delta("yes", "0.4", "-6")
    assert book.view() == {"valid": False}
    with pytest.raises(BookError):
        book.snapshot([["0.6", "5"]], [["0.5", "5"]])


def test_kraken_checksum_fixed_vector_and_decimal_precision():
    # CRC input: ask 101.00/1.250, then bid 100.00/2.500 => 101001250100002500.
    bids, asks = {Decimal("100.00"): Decimal("2.500")}, {Decimal("101.00"): Decimal("1.250")}
    assert checksum(bids, asks) == 3654185688
    book = KrakenBook()
    book.apply(
        {
            "bids": [{"price": "100.00", "qty": "2.500"}],
            "asks": [{"price": "101.00", "qty": "1.250"}],
            "checksum": 3654185688,
        },
        True,
    )
    assert book.valid
    with pytest.raises(BookError, match="checksum"):
        book.apply({"bids": [{"price": "100.00", "qty": "2.000"}], "checksum": 123})
    assert not book.view()["valid"]


def test_kraken_updates_are_absolute_quantities_and_delete_zero():
    book = KrakenBook(depth=1)
    book.apply(
        {
            "bids": [{"price": "100", "qty": "2"}],
            "asks": [{"price": "101", "qty": "3"}],
            "checksum": checksum({Decimal(100): Decimal(2)}, {Decimal(101): Decimal(3)}),
        },
        True,
    )
    book.apply(
        {
            "bids": [{"price": "99", "qty": "4"}, {"price": "100", "qty": "0"}],
            "asks": [{"price": "101", "qty": "5"}],
            "checksum": checksum({Decimal(99): Decimal(4)}, {Decimal(101): Decimal(5)}),
        }
    )
    assert book.bids == {Decimal(99): Decimal(4)}
    assert book.asks == {Decimal(101): Decimal(5)}
