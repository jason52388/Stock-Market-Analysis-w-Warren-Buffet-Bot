"""HTML-level regression tests for the dataroma scrapers.

These pin the rank-assignment and cutoff behavior that feeds the recommendation
composite score. They mock requests.get so there's no network dependency.

Key bug being guarded: dataroma's grid interleaves spacer/empty <td> cells. The
old code used the raw cell/row ordinal as `rank`, which left gaps (1,2,5,8...)
and made the max_rows cutoff stop early. rank must be a gap-free 1..N over the
*valid stock cells only*.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from warren_bot.hedge_funds import dataroma


class _Resp:
    def __init__(self, text: str):
        self.text = text

    def raise_for_status(self):
        pass


def _grid_html(stocks, *, spacers=True):
    """Build a dataroma grid page. Optionally interleave empty spacer cells
    (which is what the real page does) to exercise the rank-gap fix."""
    cells = []
    for i, (ticker, count) in enumerate(stocks):
        if spacers and i:
            cells.append("<td></td>")            # empty spacer (no <a>, no <div>)
            cells.append("<td><span>x</span></td>")  # non-link cell
        tip = f"{ticker} Inc. (Tech) Superinvestor Ownership : {count} Hold Price: $10.00"
        cells.append(f'<td><a href="#">{ticker}</a><div>{tip}</div></td>')
    return f'<table id="grid"><tr>{"".join(cells)}</tr></table>'


def _manager_html(rows):
    """Build a manager holdings page with header + spacer rows interleaved."""
    trs = ['<tr><th>h</th></tr>']  # header row: <11 cells, must be skipped
    for i, (ticker, pct, activity) in enumerate(rows):
        if i:
            trs.append('<tr><td>spacer</td></tr>')  # <11 cells -> skipped
        tds = (
            f'<td>{i+1}</td>'
            f'<td><a href="#">{ticker}<span> - {ticker} Co</span></a></td>'
            f'<td>{pct}%</td>'
            f'<td>{activity}</td>'
            f'<td>1,000,000</td>'
            f'<td>$50.00</td>'
            f'<td>$1,000,000</td>'
            f'<td>x</td>'
            f'<td>$55.00</td>'
            f'<td>10.0%</td>'
            f'<td>x</td>'
        )
        trs.append(f"<tr>{tds}</tr>")
    return f'<table id="grid"><tbody>{"".join(trs)}</tbody></table>'


class TestScrapeViewRanks:
    def test_ranks_are_gap_free_despite_spacer_cells(self):
        html = _grid_html([("AAA", 30), ("BBB", 20), ("CCC", 10)], spacers=True)
        with patch.object(dataroma.requests, "get", return_value=_Resp(html)):
            view = dataroma._scrape_view("holdings", max_rows=50)
        assert [r.ticker for r in view.rows] == ["AAA", "BBB", "CCC"]
        # The fix: contiguous 1,2,3 — NOT 1,4,7 from raw cell ordinals.
        assert [r.rank for r in view.rows] == [1, 2, 3]
        assert [r.metric_value for r in view.rows] == [30, 20, 10]

    def test_max_rows_counts_valid_cells_not_spacers(self):
        html = _grid_html([(f"T{i:02d}", 50 - i) for i in range(10)], spacers=True)
        with patch.object(dataroma.requests, "get", return_value=_Resp(html)):
            view = dataroma._scrape_view("holdings", max_rows=5)
        # Must return exactly 5 real stocks, not stop early on spacer cells.
        assert len(view.rows) == 5
        assert [r.rank for r in view.rows] == [1, 2, 3, 4, 5]
        assert view.rows[0].ticker == "T00"

    def test_ticker_dots_become_dashes(self):
        html = _grid_html([("BRK.B", 40)], spacers=False)
        with patch.object(dataroma.requests, "get", return_value=_Resp(html)):
            view = dataroma._scrape_view("holdings", max_rows=50)
        assert view.rows[0].ticker == "BRK-B"  # yfinance convention


class TestScrapeManagerRanks:
    def test_position_ranks_gap_free_despite_spacer_rows(self):
        html = _manager_html([("KO", 15.5, "Buy"), ("AAPL", 12.0, "Add 5%"),
                              ("BAC", 8.0, "")])
        with patch.object(dataroma.requests, "get", return_value=_Resp(html)):
            port = dataroma._scrape_manager_portfolio("BRK")
        assert [p.ticker for p in port.positions] == ["KO", "AAPL", "BAC"]
        # Contiguous ranks even though header + spacer rows sit between positions.
        assert [p.rank for p in port.positions] == [1, 2, 3]
        assert port.positions[0].activity_kind == "buy"
        assert port.positions[1].activity_kind == "add"
        assert port.positions[2].activity_kind == "none"


class TestViewCacheFreshness:
    def test_cache_used_when_fewer_than_max_rows(self, tmp_cache):
        """A view with fewer than max_rows real names must still be served from
        cache (the old len>=max_rows check forced a re-scrape every run)."""
        small = dataroma.HedgeFundView(
            kind="holdings",
            rows=[dataroma.HedgeFundRow("AAA", "AAA Co", "Tech", "Own", 5, 10.0, 1)],
            title="t", subtitle="s",
        )
        tmp_cache.set("dataroma", "holdings", small)
        tmp_cache.set("dataroma", "buys", small)
        tmp_cache.set("dataroma", "sells", small)
        with patch.object(dataroma, "_scrape_view") as mock_scrape:
            views = dataroma.fetch_hedge_fund_views(tmp_cache, max_rows=50)
            mock_scrape.assert_not_called()
        assert views["holdings"].rows[0].ticker == "AAA"
