"""Merge multiple :class:`SourceResult`s onto a yfinance base snapshot.

Policy (set by product decisions):
  * **Gap-fill + validate** — yfinance stays the scoring base. Secondary sources
    (EDGAR, FMP) only fill statements/rows that yfinance is *missing*; existing
    yfinance numbers are never overwritten, so the tuned scoring is preserved.
  * **Annotate + demote** — key figures (market cap, latest revenue, latest net
    income) are cross-checked. Disagreement beyond a threshold, or failure to
    corroborate against a *capable* second source, produces a flag the enrich
    stage uses to down-rank the pick.

The merged result is a new ``TickerSnapshot`` (the base is deep-copied first so
the shared cache object is never mutated) plus ``provenance`` and ``flags``.
"""
from __future__ import annotations

import copy
import logging
import math
import statistics
from dataclasses import dataclass, field

import pandas as pd

from ..analysis.statement_utils import latest, row
from .adapters.base import SourceResult
from .fetcher import TickerSnapshot
from .schema import BY_LABEL, ROWS_BY_STATEMENT

log = logging.getLogger(__name__)

_BASE_SOURCE = "yfinance"
_REQUIRED_STATEMENTS = ("income", "balance", "cashflow")

# Key figures we cross-check, and which sources can structurally supply each.
# EDGAR has no market price/cap; Finnhub (free) has no statements. Demotion only
# fires when *capable* sources fail to corroborate — never for a figure no
# enabled second source could ever provide.
HIGH_FIELDS = {"market_cap", "revenue_latest", "net_income_latest"}
CAPABILITY = {
    "price": {"yfinance", "finnhub", "fmp"},
    "market_cap": {"yfinance", "finnhub", "fmp"},
    "shares_outstanding": {"yfinance", "finnhub", "fmp", "edgar"},
    "revenue_latest": {"yfinance", "edgar", "fmp"},
    "net_income_latest": {"yfinance", "edgar", "fmp"},
}


@dataclass
class ValidationFlag:
    field: str                       # e.g. "market_cap"
    kind: str                        # "conflict" | "unconfirmed"
    severity: str                    # "high" | "medium"
    values: dict[str, float]         # source -> value
    pct_diff: float | None = None    # spread vs median, for conflicts

    def describe(self) -> str:
        vs = ", ".join(f"{s}={v:,.4g}" for s, v in sorted(self.values.items()))
        if self.kind == "conflict":
            return f"{self.field}: sources disagree {self.pct_diff:.1f}% ({vs})"
        return f"{self.field}: not corroborated ({vs})"


@dataclass
class MergeResult:
    snapshot: TickerSnapshot
    provenance: dict[str, str] = field(default_factory=dict)
    flags: list[ValidationFlag] = field(default_factory=list)


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not (isinstance(v, float) and math.isnan(v))


def _price(info: dict) -> float | None:
    return info.get("currentPrice") or info.get("regularMarketPrice") or info.get("previousClose")


def _key_figures(income, balance, info=None, quote=None) -> dict[str, float | None]:
    info = info or {}
    quote = quote or {}
    return {
        "price": (quote.get("price") if quote else None) or _price(info),
        "market_cap": (quote.get("market_cap") if quote else None) or info.get("marketCap"),
        "shares_outstanding": (quote.get("shares_outstanding") if quote else None)
        or info.get("sharesOutstanding"),
        "revenue_latest": latest(row(income, BY_LABEL["Total Revenue"].aliases)),
        "net_income_latest": latest(row(income, BY_LABEL["Net Income"].aliases)),
    }


def _fill_missing_rows(base_df: pd.DataFrame, src_df: pd.DataFrame | None,
                       labels: list[str], source: str, stmt: str,
                       provenance: dict[str, str]) -> None:
    """Append only canonical rows that are ABSENT from ``base_df``, aligning the
    source values onto the base's columns by fiscal year. Never overwrites."""
    if src_df is None or src_df.empty:
        return
    year_to_col: dict[int, object] = {}
    for col in base_df.columns:
        try:
            year_to_col[pd.Timestamp(col).year] = col
        except (ValueError, TypeError):
            continue
    if not year_to_col:
        return
    for label in labels:
        aliases = BY_LABEL[label].aliases
        if row(base_df, aliases) is not None:
            continue  # yfinance already has it — leave untouched
        src_row = row(src_df, aliases)
        if src_row is None:
            continue
        new = pd.Series(index=base_df.columns, dtype="float64")
        for idx, val in src_row.items():
            try:
                col = year_to_col.get(pd.Timestamp(idx).year)
            except (ValueError, TypeError):
                col = None
            if col is not None and _is_num(val):
                new[col] = float(val)
        if new.notna().any():
            base_df.loc[label] = new
            provenance[f"{stmt}:{label}"] = source


def _validate(consulted: set[str], figures_by_source: dict[str, dict],
              *, divergence_pct: float) -> list[ValidationFlag]:
    flags: list[ValidationFlag] = []
    all_fields = set().union(*(f.keys() for f in figures_by_source.values())) \
        if figures_by_source else set()
    for fld in sorted(all_fields):
        capable = CAPABILITY.get(fld, set()) & consulted
        reported = {s: float(f[fld]) for s, f in figures_by_source.items()
                    if _is_num(f.get(fld))}
        if not reported:
            continue
        severity = "high" if fld in HIGH_FIELDS else "medium"
        if len(capable) < 2:
            continue  # only one source could ever supply this — nothing to corroborate
        if len(reported) >= 2:
            med = statistics.median(reported.values())
            if med == 0:
                continue
            spread = (max(reported.values()) - min(reported.values())) / abs(med) * 100.0
            if spread > divergence_pct:
                flags.append(ValidationFlag(fld, "conflict", severity, reported, spread))
        elif severity == "high":
            # A capable second source existed but didn't corroborate a KEY figure.
            # We only raise this for high-severity fields (market cap, revenue, net
            # income); a second source not echoing price/shares is common and not
            # actionable, so medium 'unconfirmed' is suppressed to keep the signal clean.
            flags.append(ValidationFlag(fld, "unconfirmed", severity, reported))
    return flags


def merge(base: TickerSnapshot, secondary: list[SourceResult],
          *, divergence_pct: float = 5.0, fill_rows: bool = True) -> MergeResult:
    """Merge ``secondary`` source results onto the yfinance ``base`` snapshot.

    ``secondary`` is in precedence order: earlier sources win ties when filling a
    statement that yfinance lacks entirely. Returns a :class:`MergeResult` with a
    deep-copied, gap-filled snapshot plus provenance and validation flags.
    """
    snap = copy.deepcopy(base)
    provenance: dict[str, str] = {}
    consulted = {_BASE_SOURCE} | {r.source for r in secondary}

    for stmt in _REQUIRED_STATEMENTS:
        base_df = getattr(snap, stmt)
        have_base = base_df is not None and not base_df.empty
        provenance[stmt] = _BASE_SOURCE if have_base else "missing"
        if not have_base:
            # Wholesale rescue: take the statement from the first source that has it.
            for res in secondary:
                src_df = res.statement(stmt)
                if src_df is not None and not src_df.empty:
                    setattr(snap, stmt, src_df.copy())
                    provenance[stmt] = res.source
                    break
        elif fill_rows:
            for res in secondary:
                _fill_missing_rows(getattr(snap, stmt), res.statement(stmt),
                                   ROWS_BY_STATEMENT[stmt], res.source, stmt, provenance)

    # If yfinance had no income but a secondary supplied it, clear the stale
    # completeness error so the re-score can proceed (handled by the caller too).
    figures_by_source: dict[str, dict] = {
        _BASE_SOURCE: _key_figures(snap.income, snap.balance, info=snap.info),
    }
    for res in secondary:
        figs = _key_figures(res.income, res.balance, quote=res.quote)
        if any(_is_num(v) for v in figs.values()):
            figures_by_source[res.source] = figs

    flags = _validate(consulted, figures_by_source, divergence_pct=divergence_pct)
    snap.provenance = provenance
    snap.flags = [f.describe() for f in flags]
    return MergeResult(snapshot=snap, provenance=provenance, flags=flags)


def summarize_quality(provenance: dict[str, str],
                      flags: list[ValidationFlag]) -> dict | None:
    """Build one data-quality summary used by every dashboard surface so the
    badge is identical everywhere. Returns None when a pick wasn't enriched.

    level: ``ok`` (corroborated, all yfinance), ``info`` (gap-filled or minor
    disagreement), ``warn`` (a high-severity key-figure conflict / non-corroboration).
    """
    if not provenance and not flags:
        return None
    gapfilled = {k: v for k, v in provenance.items() if v not in ("yfinance", "missing")}
    sources = sorted({v for v in provenance.values() if v not in ("missing",)}
                     | {f_src for f in flags for f_src in f.values})
    has_high = any(f.severity == "high" for f in flags)
    if has_high:
        level = "warn"
    elif gapfilled or flags:
        level = "info"
    else:
        level = "ok"
    label = {"ok": "Verified", "info": "Cross-checked", "warn": "Check data"}[level]
    detail: list[str] = []
    if gapfilled:
        filled = [f"{k.split(':')[-1]} ← {v}" for k, v in gapfilled.items()]
        detail.append("Gap-filled: " + ", ".join(filled))
    detail.extend(f.describe() for f in flags)
    if not detail:
        detail.append("Key figures corroborated across sources: " + ", ".join(sources))
    return {
        "level": level,
        "label": label,
        "sources": sources,
        "flags": [f.describe() for f in flags],
        "detail": detail,
    }
