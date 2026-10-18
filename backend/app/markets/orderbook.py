"""Decimal books. Kalshi asks are complementary opposing bids; Kraken uses CRC32."""

import zlib
from decimal import Decimal


def number(value):
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("Non-finite market value")
    return result


class BookError(ValueError):
    pass


def level_map(rows, binary=False):
    levels = {}
    for price, quantity in rows:
        p, q = number(price), number(quantity)
        if p <= 0 or (binary and p >= 1) or q < 0:
            raise BookError("Invalid book level")
        if q:
            levels[p] = q
    return levels


def levels_view(levels, reverse=False):
    return [[p, levels[p]] for p in sorted(levels, reverse=reverse)]


class KalshiBook:
    def __init__(self):
        self.yes, self.no = {}, {}
        self.valid = False

    def snapshot(self, yes, no):
        self.valid = False
        yes, no = level_map(yes, True), level_map(no, True)
        if yes and no and max(yes) + max(no) > 1:
            self.valid = False
            raise BookError("Crossed Kalshi book")
        self.yes, self.no, self.valid = yes, no, True

    def delta(self, side, price, delta):
        if not self.valid or side not in {"yes", "no"}:
            raise BookError("Snapshot required before delta")
        yes, no = self.yes.copy(), self.no.copy()
        book = yes if side == "yes" else no
        p = number(price)
        q = book.get(p, Decimal(0)) + number(delta)
        if not 0 < p < 1 or q < 0:
            self.valid = False
            raise BookError("Invalid Kalshi delta")
        if q:
            book[p] = q
        else:
            book.pop(p, None)
        self.snapshot(list(yes.items()), list(no.items()))

    def view(self):
        if not self.valid:
            return {"valid": False}
        return {
            "valid": True,
            "yes_bids": levels_view(self.yes, True),
            "yes_asks": levels_view({1 - p: q for p, q in self.no.items()}),
            "no_bids": levels_view(self.no, True),
            "no_asks": levels_view({1 - p: q for p, q in self.yes.items()}),
        }


def checksum(bids, asks):
    def digits(value):
        return format(value, "f").replace(".", "").lstrip("0")

    content = "".join(
        digits(p) + digits(q)
        for book, reverse in [(asks, False), (bids, True)]
        for p, q in sorted(book.items(), reverse=reverse)[:10]
    )
    return zlib.crc32(content.encode()) & 0xFFFFFFFF


class KrakenBook:
    def __init__(self, depth=10):
        self.depth, self.bids, self.asks = depth, {}, {}
        self.valid = False

    def apply(self, data, snapshot=False):
        if not snapshot and not self.valid:
            raise BookError("Kraken snapshot required")
        self.valid = False
        bids, asks = ({}, {}) if snapshot else (self.bids.copy(), self.asks.copy())
        for side, book in [("bids", bids), ("asks", asks)]:
            for row in data.get(side, []):
                p, q = number(row["price"]), number(row["qty"])
                if p <= 0 or q < 0:
                    self.valid = False
                    raise BookError("Invalid Kraken level")
                if q:
                    book[p] = q
                else:
                    book.pop(p, None)
        bids = dict(sorted(bids.items(), reverse=True)[: self.depth])
        asks = dict(sorted(asks.items())[: self.depth])
        if not bids or not asks or max(bids) >= min(asks):
            self.valid = False
            raise BookError("Empty or crossed Kraken book")
        if "checksum" not in data or checksum(bids, asks) != int(data["checksum"]):
            self.valid = False
            raise BookError("Kraken checksum mismatch; resubscribe for snapshot")
        self.bids, self.asks, self.valid = bids, asks, True

    def view(self):
        return {
            "valid": self.valid,
            "bids": levels_view(self.bids, True) if self.valid else [],
            "asks": levels_view(self.asks) if self.valid else [],
        }
