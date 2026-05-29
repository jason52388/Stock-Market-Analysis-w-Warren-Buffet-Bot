"""Tests for the dataroma string parsers — no network."""
from __future__ import annotations

import pytest

from warren_bot.hedge_funds.dataroma import (
    _classify_activity,
    _num,
    _parse_tooltip,
)


class TestParseTooltip:
    def test_holdings_tooltip(self):
        text = ("Alphabet Inc. (Information Technology) "
                "Superinvestor Ownership : 39 Hold Price: $287.56")
        name, sector, label, value, price = _parse_tooltip(text)
        assert name == "Alphabet Inc."
        assert sector == "Information Technology"
        assert "Ownership" in label
        assert value == 39
        assert price == pytest.approx(287.56)

    def test_buys_tooltip(self):
        text = "Microsoft Corp. (Information Technology) No. of Buys/Adds: 19 Hold Price: $370.22"
        name, sector, label, value, price = _parse_tooltip(text)
        assert name == "Microsoft Corp."
        assert "Buys" in label
        assert value == 19
        assert price == pytest.approx(370.22)

    def test_sells_tooltip_no_hold_price(self):
        # Some sell-view tooltips omit the price
        text = "Alphabet Inc. (Information Technology) No. of Sells/Reductions: 24"
        name, sector, label, value, price = _parse_tooltip(text)
        assert name == "Alphabet Inc."
        assert "Sells" in label
        assert value == 24
        assert price is None

    def test_handles_comma_in_number(self):
        text = "Big Cap Inc. (Sector) Holders : 1,234 Hold Price: $1,234.56"
        _, _, _, value, price = _parse_tooltip(text)
        assert value == 1234
        assert price == pytest.approx(1234.56)


class TestNum:
    @pytest.mark.parametrize("text,expected", [
        ("$57,843,261,000", 57_843_261_000.0),
        ("227,917,808", 227_917_808.0),
        ("21.99", 21.99),
        ("-3.5%", -3.5),
        ("", None),
        ("not a number", None),
        ("$ ", None),
    ])
    def test_num_parse(self, text, expected):
        assert _num(text) == expected


class TestClassifyActivity:
    @pytest.mark.parametrize("text,expected_kind", [
        ("Buy", "buy"),
        ("Buy 100%", "buy"),
        ("Add 43.24%", "add"),
        ("Sell", "sell"),
        ("Sell -100.00%", "sell"),
        ("Reduce -5.22%", "reduce"),
        ("", "none"),
        ("   ", "none"),
        ("Hold", "none"),  # unknown verbs fall through
    ])
    def test_classify(self, text, expected_kind):
        label, kind = _classify_activity(text)
        assert kind == expected_kind
        if expected_kind != "none":
            assert label == text.strip()
