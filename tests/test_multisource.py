"""Unified multi-source data model: schema, EDGAR parsing, merge, enrich."""
from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest

from warren_bot.data import schema
from warren_bot.data.adapters.base import SourceResult
from warren_bot.data.adapters.edgar import EdgarAdapter
from warren_bot.data.cache import Cache
from warren_bot.data.enrich import _penalty_for, enrich_snapshot
from warren_bot.data.fetcher import TickerSnapshot
from warren_bot.data.merge import ValidationFlag, merge


def _df(rows: dict[str, list[float]], years=(2021, 2022, 2023)) -> pd.DataFrame:
    cols = [pd.Timestamp(f"{y}-12-31") for y in years]
    return pd.DataFrame({label: vals for label, vals in rows.items()},
                        index=cols).T  # rows=labels, cols=dates


# --- schema -----------------------------------------------------------------
class TestSchema:
    def test_canonical_label_matches_aliases_case_insensitively(self):
        assert schema.canonical_label("TotalRevenue") == "Total Revenue"
        assert schema.canonical_label("net income") == "Net Income"
        assert schema.canonical_label("NetIncomeLoss is not an alias") is None

    def test_build_statement_orients_labels_on_rows_dates_on_cols(self):
        s = pd.Series({pd.Timestamp("2022-12-31"): 10.0, pd.Timestamp("2021-12-31"): 8.0})
        df = schema.build_statement({"Total Revenue": s})
        assert list(df.index) == ["Total Revenue"]
        assert list(df.columns) == [pd.Timestamp("2021-12-31"), pd.Timestamp("2022-12-31")]

    def test_capex_metric_negates_edgar(self):
        assert schema.BY_LABEL["Capital Expenditure"].negate_edgar is True


# --- EDGAR adapter ----------------------------------------------------------
_FACTS = {
    "facts": {"us-gaap": {
        "Revenues": {"units": {"USD": [
            {"end": "2022-12-31", "start": "2022-01-01", "val": 100, "fy": 2022, "fp": "FY", "form": "10-K", "filed": "2023-02-01"},
            {"end": "2023-12-31", "start": "2023-01-01", "val": 120, "fy": 2023, "fp": "FY", "form": "10-K", "filed": "2024-02-01"},
            {"end": "2023-06-30", "start": "2023-04-01", "val": 30, "fy": 2023, "fp": "Q2", "form": "10-Q", "filed": "2023-07-01"},
        ]}},
        "NetIncomeLoss": {"units": {"USD": [
            {"end": "2023-12-31", "start": "2023-01-01", "val": 20, "fy": 2023, "fp": "FY", "form": "10-K", "filed": "2024-02-01"},
        ]}},
        "Assets": {"units": {"USD": [
            {"end": "2023-12-31", "val": 500, "fy": 2023, "fp": "FY", "form": "10-K", "filed": "2024-02-01"},
        ]}},
        "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": [
            {"end": "2023-12-31", "start": "2023-01-01", "val": 40, "fy": 2023, "fp": "FY", "form": "10-K", "filed": "2024-02-01"},
        ]}},
        "PaymentsToAcquirePropertyPlantAndEquipment": {"units": {"USD": [
            {"end": "2023-12-31", "start": "2023-01-01", "val": 15, "fy": 2023, "fp": "FY", "form": "10-K", "filed": "2024-02-01"},
        ]}},
    }}
}
_CIKMAP = {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}}


class TestEdgarAdapter:
    def _adapter(self, tmp_path):
        return EdgarAdapter(Cache(tmp_path / "c.sqlite", 3600), user_agent="test (x@y.com)")

    def _dispatch(self, url, **kw):
        return _CIKMAP if "company_tickers" in url else _FACTS

    def test_parses_annual_statements_with_canonical_labels(self, tmp_path):
        a = self._adapter(tmp_path)
        with patch("warren_bot.data.adapters.edgar.get_json", side_effect=self._dispatch):
            res = a.fetch("AAPL")
        assert "Total Revenue" in res.income.index
        assert "Net Income" in res.income.index
        assert "Total Assets" in res.balance.index
        assert "Operating Cash Flow" in res.cashflow.index
        # quarterly Q2 row excluded -> only the two annual revenue points
        rev = res.income.loc["Total Revenue"].dropna()
        assert sorted(rev.values.tolist()) == [100, 120]
        a.cache.close()

    def test_capex_sign_is_negated_to_match_yfinance(self, tmp_path):
        a = self._adapter(tmp_path)
        with patch("warren_bot.data.adapters.edgar.get_json", side_effect=self._dispatch):
            res = a.fetch("AAPL")
        capex = res.cashflow.loc["Capital Expenditure"].dropna().iloc[0]
        assert capex == -15  # EDGAR reports +15 outflow; we flip it

    def test_disabled_without_user_agent(self, tmp_path):
        a = EdgarAdapter(Cache(tmp_path / "c.sqlite", 3600), user_agent="")
        assert a.enabled is False
        assert a.fetch("AAPL").error
        a.cache.close()


# --- merge: gap-fill --------------------------------------------------------
class TestMergeGapFill:
    def test_missing_statement_is_filled_wholesale_and_recorded(self):
        base = TickerSnapshot(
            ticker="X",
            income=_df({"Total Revenue": [100, 110, 120], "Net Income": [10, 11, 12]}),
            balance=_df({"Total Assets": [500, 510, 520]}),
            cashflow=None,  # missing -> rescue target
            price_history=pd.DataFrame({"Close": [1, 2]}),
            info={"marketCap": 1000},
        )
        edgar = SourceResult("edgar", cashflow=_df({
            "Operating Cash Flow": [40, 44, 48], "Capital Expenditure": [-5, -6, -7]}))
        mr = merge(base, [edgar])
        assert mr.snapshot.cashflow is not None
        assert "Operating Cash Flow" in mr.snapshot.cashflow.index
        assert mr.provenance["cashflow"] == "edgar"
        assert mr.provenance["income"] == "yfinance"

    def test_missing_row_filled_without_overwriting_existing(self):
        base = TickerSnapshot(
            ticker="X",
            income=_df({"Total Revenue": [100, 110, 120]}),  # no Net Income
            info={"marketCap": 1000},
        )
        edgar = SourceResult("edgar", income=_df({
            "Total Revenue": [999, 999, 999], "Net Income": [10, 11, 12]}))
        mr = merge(base, [edgar])
        # existing yfinance revenue untouched, net income added from edgar
        assert mr.snapshot.income.loc["Total Revenue"].tolist() == [100, 110, 120]
        assert "Net Income" in mr.snapshot.income.index
        assert mr.provenance["income:Net Income"] == "edgar"


# --- merge: validation / corroboration --------------------------------------
class TestMergeValidation:
    def _base(self, mcap=1000):
        return TickerSnapshot(ticker="X",
                              income=_df({"Total Revenue": [100, 110, 120],
                                          "Net Income": [10, 11, 12]}),
                              info={"marketCap": mcap, "regularMarketPrice": 50})

    def test_conflict_flag_when_market_cap_diverges(self):
        finnhub = SourceResult("finnhub", quote={"market_cap": 1600, "price": 50})
        mr = merge(self._base(mcap=1000), [finnhub], divergence_pct=5.0)
        kinds = {(f.field, f.kind) for f in mr.flags}
        assert ("market_cap", "conflict") in kinds

    def test_agreement_within_threshold_produces_no_flag(self):
        finnhub = SourceResult("finnhub", quote={"market_cap": 1020, "price": 50})
        mr = merge(self._base(mcap=1000), [finnhub], divergence_pct=5.0)
        assert all(f.field != "market_cap" for f in mr.flags)

    def test_edgar_only_does_not_flag_market_cap_it_cannot_supply(self):
        # EDGAR is incapable of market cap; with no capable 2nd source it must
        # NOT be flagged as unconfirmed (else every pick would be demoted).
        edgar = SourceResult("edgar", income=_df({"Total Revenue": [100, 110, 120],
                                                   "Net Income": [10, 11, 12]}))
        mr = merge(self._base(mcap=1000), [edgar], divergence_pct=5.0)
        assert all(f.field != "market_cap" for f in mr.flags)

    def test_unconfirmed_revenue_when_capable_source_lacks_it(self):
        # FMP is capable of revenue but returns none -> yfinance uncorroborated.
        fmp = SourceResult("fmp", quote={"price": 50})
        mr = merge(self._base(mcap=1000), [fmp], divergence_pct=5.0)
        rev = [f for f in mr.flags if f.field == "revenue_latest"]
        assert rev and rev[0].kind == "unconfirmed" and rev[0].severity == "high"

    def test_medium_field_unconfirmed_is_suppressed(self):
        # Finnhub (capable of price) is consulted but returns no price -> price is
        # an unconfirmed MEDIUM field, which must NOT be flagged (not actionable).
        finnhub = SourceResult("finnhub", quote={"market_cap": 1010})
        mr = merge(self._base(mcap=1000), [finnhub], divergence_pct=5.0)
        assert all(f.field != "price" for f in mr.flags)

    def test_base_is_not_mutated(self):
        base = self._base(mcap=1000)
        finnhub = SourceResult("finnhub", quote={"market_cap": 1600})
        merge(base, [finnhub])
        assert not base.provenance and not base.flags  # original untouched


# --- enrich -----------------------------------------------------------------
class _StubAdapter:
    name = "edgar"
    enabled = True

    def __init__(self, result):
        self._result = result

    def fetch(self, ticker):
        return self._result


class TestEnrich:
    def test_penalty_mapping_and_cap(self):
        flags = [ValidationFlag("market_cap", "conflict", "high", {}),       # 8
                 ValidationFlag("revenue_latest", "unconfirmed", "high", {})]  # 4 -> cap 12
        assert _penalty_for(flags) == 12.0
        assert _penalty_for([ValidationFlag("price", "conflict", "medium", {})]) == 2.0

    def test_rescue_clears_stale_incomplete_error(self):
        snap = TickerSnapshot(
            ticker="X",
            income=_df({"Total Revenue": [100, 110, 120], "Net Income": [1, 2, 3]}),
            balance=_df({"Total Assets": [500, 510, 520]}),
            cashflow=None,
            price_history=pd.DataFrame({"Close": [1, 2]}),
            error="incomplete data: missing cashflow",
            info={"marketCap": 1000},
        )
        edgar = _StubAdapter(SourceResult("edgar", cashflow=_df({
            "Operating Cash Flow": [40, 44, 48], "Capital Expenditure": [-5, -6, -7]})))
        merged, flags = enrich_snapshot(snap, [edgar])
        assert merged.error is None          # stale gate error cleared after fill
        assert merged.cashflow is not None
        assert merged.missing_statements() == []

    def test_no_adapters_is_noop(self):
        snap = TickerSnapshot(ticker="X", info={})
        merged, flags = enrich_snapshot(snap, [])
        assert merged is snap and flags == []


def test_effective_total_applies_penalty():
    from warren_bot.analysis.scorer import TickerScore
    ts = TickerScore(ticker="X", name="X", sector="", total=80.0, dimensions=[],
                     ratios=None, growth=None, valuation=None)
    assert ts.effective_total == 80.0
    ts.corroboration_penalty = 12.0
    assert ts.effective_total == 68.0
