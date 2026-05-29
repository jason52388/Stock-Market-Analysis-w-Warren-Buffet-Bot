"""Tier-2 enrichment: validate and gap-fill the picks that matter.

The universe sweep (tier 1) stays on free yfinance. This stage runs AFTER scoring
on a small set — the top-N finalists plus any names the completeness gate dropped
— and consults the secondary sources (EDGAR, FMP, Finnhub) to:

  * **rescue** gate-failures by gap-filling missing statements from EDGAR/FMP and
    re-scoring, so genuinely good names that yfinance flaked on rejoin the pool;
  * **validate** finalists by cross-checking key figures and attaching provenance
    + disagreement flags; and
  * **demote** finalists whose key figures can't be corroborated by a capable
    second source (per the "annotate + demote" policy).

Secondary calls are bounded to dozens per run, well within free-tier limits.
"""
from __future__ import annotations

import logging

from ..analysis.scorer import score_ticker
from .adapters.base import SourceAdapter, SourceResult
from .cache import Cache
from .fetcher import TickerSnapshot
from .merge import ValidationFlag, merge

log = logging.getLogger(__name__)

# Default SEC User-Agent when none is configured. SEC asks for a descriptive
# string with a contact; override via the SEC_USER_AGENT env var to use your own.
_DEFAULT_SEC_USER_AGENT = (
    "warren-bot/1.0 (+https://github.com/jason52388/stock-market-analysis-w-warren-buffet-bot)"
)

# Per-flag ranking penalty (points off the 0-100 score), capped per pick.
_PENALTY = {("conflict", "high"): 8.0, ("conflict", "medium"): 2.0,
            ("unconfirmed", "high"): 4.0, ("unconfirmed", "medium"): 0.0}
_PENALTY_CAP = 12.0


def build_adapters(settings: dict, cache: Cache) -> list[SourceAdapter]:
    """Construct the enabled secondary adapters in precedence order
    (EDGAR first — authoritative filings — then FMP, then Finnhub)."""
    src = settings.get("sources", {}) or {}
    adapters: list[SourceAdapter] = []

    edgar_cfg = src.get("edgar", {}) or {}
    if edgar_cfg.get("enabled", True):
        from .adapters.edgar import EdgarAdapter
        # SEC requires a declared, descriptive User-Agent with a contact. Prefer
        # the configured/env value; fall back to a reachable default so EDGAR
        # (no API key needed) works out of the box.
        ua = edgar_cfg.get("user_agent") or _DEFAULT_SEC_USER_AGENT
        adapters.append(EdgarAdapter(
            cache, user_agent=ua,
            requests_per_sec=float(edgar_cfg.get("requests_per_sec", 8.0)),
            facts_ttl_seconds=int(edgar_cfg.get("facts_ttl_hours", 720)) * 3600,
        ))

    fmp_cfg = src.get("fmp", {}) or {}
    if fmp_cfg.get("enabled", True) and fmp_cfg.get("api_key"):
        from .adapters.fmp import FmpAdapter
        cap = fmp_cfg.get("max_fetches_per_run")
        adapters.append(FmpAdapter(
            cache, api_key=fmp_cfg.get("api_key", ""),
            requests_per_sec=float(fmp_cfg.get("requests_per_sec", 4.0)),
            max_fetches=(int(cap) if cap is not None else None)))

    finnhub_cfg = src.get("finnhub", {}) or {}
    if finnhub_cfg.get("enabled", True) and finnhub_cfg.get("api_key"):
        from .adapters.finnhub import FinnhubAdapter
        adapters.append(FinnhubAdapter(
            cache, api_key=finnhub_cfg.get("api_key", ""),
            requests_per_sec=float(finnhub_cfg.get("requests_per_sec", 1.0))))

    return [a for a in adapters if a.enabled]


def _gather(adapters: list[SourceAdapter], ticker: str) -> list[SourceResult]:
    out: list[SourceResult] = []
    for a in adapters:
        try:
            res = a.fetch(ticker)
            if res is not None:
                out.append(res)
        except Exception as e:  # an adapter must never break the run
            log.debug("adapter %s failed for %s: %s", a.name, ticker, e)
    return out


def _penalty_for(flags: list[ValidationFlag]) -> float:
    total = sum(_PENALTY.get((f.kind, f.severity), 0.0) for f in flags)
    return min(total, _PENALTY_CAP)


def enrich_snapshot(snap: TickerSnapshot, adapters: list[SourceAdapter],
                    *, divergence_pct: float = 5.0,
                    field_divergence: dict[str, float] | None = None):
    """Merge secondary sources onto ``snap``. Returns (merged_snapshot, flags)."""
    results = _gather(adapters, snap.ticker)
    if not results:
        return snap, []
    mr = merge(snap, results, divergence_pct=divergence_pct,
               field_divergence=field_divergence)
    out = mr.snapshot
    # If a secondary rescued a previously-missing statement, drop the stale
    # "incomplete data" error so the re-score can run.
    if out.error and str(out.error).startswith("incomplete data") and not out.missing_statements():
        out.error = None
    return out, mr.flags
