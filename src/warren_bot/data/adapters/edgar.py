"""SEC EDGAR adapter — authoritative XBRL fundamentals, no API key required.

Maps a ticker to its CIK via the public company_tickers.json, then pulls the
``companyfacts`` XBRL bundle and reduces each us-gaap concept to an annual series
keyed by fiscal-period-end date. SEC's fair-access policy requires a declared
``User-Agent`` (name + contact) and asks callers to stay under ~10 req/s.
"""
from __future__ import annotations

import logging

import pandas as pd

from .. import schema
from ..cache import Cache
from ..schema import BALANCE, CASHFLOW, INCOME, METRICS
from ._http import RateLimiter, get_json
from .base import SourceAdapter, SourceResult

log = logging.getLogger(__name__)

_CIK_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
_ANNUAL_FORMS = ("10-K", "20-F", "40-F")


class EdgarAdapter(SourceAdapter):
    name = "edgar"

    def __init__(self, cache: Cache, *, user_agent: str,
                 requests_per_sec: float = 8.0,
                 facts_ttl_seconds: int = 30 * 24 * 3600,
                 cikmap_ttl_seconds: int = 7 * 24 * 3600,
                 enabled: bool = True):
        super().__init__(enabled=enabled and bool(user_agent))
        self.cache = cache
        self.user_agent = user_agent
        self.facts_ttl = facts_ttl_seconds
        self.cikmap_ttl = cikmap_ttl_seconds
        self.limiter = RateLimiter(requests_per_sec)

    @property
    def _headers(self) -> dict:
        return {"User-Agent": self.user_agent, "Accept": "application/json"}

    # --- CIK resolution ------------------------------------------------------
    def _cik_map(self) -> dict[str, int]:
        cached = self.cache.get("edgar_cikmap", "all", ttl_override=self.cikmap_ttl)
        if cached is not None:
            return cached
        raw = get_json(_CIK_MAP_URL, headers=self._headers, limiter=self.limiter)
        out: dict[str, int] = {}
        if isinstance(raw, dict):
            for row in raw.values():
                t = str(row.get("ticker", "")).upper()
                if t:
                    out[t] = int(row["cik_str"])
        if out:
            self.cache.set("edgar_cikmap", "all", out)
        return out

    def _cik(self, ticker: str) -> int | None:
        return self._cik_map().get(ticker.upper())

    def _companyfacts(self, cik: int) -> dict | None:
        key = f"{cik:010d}"
        cached = self.cache.get("edgar_facts", key, ttl_override=self.facts_ttl)
        if cached is not None:
            return cached
        data = get_json(_FACTS_URL.format(cik=cik), headers=self._headers, limiter=self.limiter)
        if isinstance(data, dict) and data.get("facts"):
            self.cache.set("edgar_facts", key, data)
            return data
        return None

    # --- fetch ---------------------------------------------------------------
    def fetch(self, ticker: str) -> SourceResult:
        if not self.enabled:
            return SourceResult(self.name, error="edgar disabled")
        cik = self._cik(ticker)
        if cik is None:
            return SourceResult(self.name, error="no CIK for ticker")
        facts = self._companyfacts(cik)
        if not facts:
            return SourceResult(self.name, error="no companyfacts")
        gaap = facts.get("facts", {}).get("us-gaap", {})

        def stmt(which: str) -> pd.DataFrame | None:
            rows = {}
            for m in METRICS:
                if m.statement != which or not m.edgar:
                    continue
                s = _annual_series(gaap, m.edgar, negate=m.negate_edgar)
                if s is not None:
                    rows[m.label] = s
            return schema.build_statement(rows)

        income = stmt(INCOME)
        balance = stmt(BALANCE)
        cashflow = stmt(CASHFLOW)

        quote: dict[str, float] = {}
        shares = _annual_series(gaap, schema.BY_LABEL["Ordinary Shares Number"].edgar)
        if shares is not None and len(shares):
            quote["shares_outstanding"] = float(shares.sort_index().iloc[-1])

        result = SourceResult(self.name, income=income, balance=balance,
                              cashflow=cashflow, quote=quote)
        if not result.has_statements:
            result.error = "no annual statements parsed"
        return result


def _annual_series(gaap: dict, tags: list[str], *, negate: bool = False) -> pd.Series | None:
    """Reduce the first present us-gaap ``tag`` to one value per fiscal year.

    Keeps only annual figures from 10-K/20-F/40-F filings (``fp == 'FY'``), drops
    sub-annual periods for flow concepts (start..end shorter than ~10 months),
    and on amendments keeps the latest-filed value. Indexed by period-end date.
    """
    for tag in tags:
        node = gaap.get(tag)
        if not node:
            continue
        entries = []
        for arr in node.get("units", {}).values():
            entries.extend(arr)
        by_year: dict[int, tuple[pd.Timestamp, float, str]] = {}
        for e in entries:
            form = str(e.get("form", ""))
            val = e.get("val")
            end = e.get("end")
            if val is None or end is None:
                continue
            if not form.startswith(_ANNUAL_FORMS):
                continue
            if e.get("fp") not in (None, "FY"):
                continue
            start = e.get("start")
            if start:  # flow concept — require a roughly full-year window
                try:
                    if (pd.Timestamp(end) - pd.Timestamp(start)).days < 300:
                        continue
                except (ValueError, TypeError):
                    pass
            try:
                end_ts = pd.Timestamp(end)
            except (ValueError, TypeError):
                continue
            fy = e.get("fy") or end_ts.year
            filed = str(e.get("filed", ""))
            prev = by_year.get(fy)
            if prev is None or filed >= prev[2]:
                by_year[fy] = (end_ts, float(val), filed)
        if by_year:
            data = {end_ts: (-v if negate else v) for (end_ts, v, _f) in by_year.values()}
            return pd.Series(data).sort_index()
    return None
