"""End-to-end orchestration: load universe -> fetch -> score -> thesis -> deliver."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from .analysis.scorer import TickerScore, score_ticker
from .config import load_settings, repo_root
from .data.cache import Cache
from .data.fetcher import Fetcher, TickerSnapshot
from .news.stock_news import NewsItem, fetch_stock_news
from .thesis.generator import Thesis, generate_thesis

log = logging.getLogger(__name__)


@dataclass
class Pick:
    score: TickerScore
    thesis: Thesis
    snap_info: dict
    # Multi-source enrichment (empty unless the enrich stage ran on this pick).
    provenance: dict = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)
    dq: dict | None = None      # data-quality badge summary (data.merge.summarize_quality)


def load_universe(settings: dict) -> list[str]:
    root = repo_root()
    tickers: list[str] = []
    seen: set[str] = set()
    for path in settings["universe"]["files"]:
        p = root / path
        if not p.exists():
            log.warning("Universe file missing: %s", p)
            continue
        for line in p.read_text().splitlines():
            t = line.strip().upper()
            if t and not t.startswith("#") and t not in seen:
                tickers.append(t)
                seen.add(t)
    return tickers


def build_cache(settings: dict) -> Cache:
    """Build the shared SQLite cache. Reused by the fetcher AND by dataroma
    so we don't open two connections to the same file."""
    data_cfg = settings["data"]
    root = repo_root()
    cache_path = root / data_cfg["cache_path"]
    return Cache(cache_path, ttl_seconds=int(data_cfg["cache_ttl_hours"]) * 3600)


def _build_fetcher(settings: dict, cache: Cache | None = None) -> Fetcher:
    data_cfg = settings["data"]
    thr = settings.get("throttle", {})
    cache = cache or build_cache(settings)
    stmt_ttl_h = data_cfg.get("statement_ttl_hours")
    price_ttl_h = data_cfg.get("price_ttl_hours")
    return Fetcher(
        cache,
        batch_size=int(data_cfg["yf_batch_size"]),
        batch_sleep_sec=float(data_cfg["yf_batch_sleep_sec"]),
        min_market_cap=float(data_cfg.get("min_market_cap_usd", 0) or 0),
        statement_ttl_seconds=(int(stmt_ttl_h) * 3600 if stmt_ttl_h is not None else None),
        price_ttl_seconds=(int(price_ttl_h) * 3600 if price_ttl_h is not None else None),
        requests_per_sec=float(thr.get("requests_per_sec", 0) or 0),
        blank_retries=int(thr.get("blank_retries", 0) or 0),
        blank_retry_backoff_sec=float(thr.get("blank_retry_backoff_sec", 1.5)),
        yf_internal_retries=int(thr.get("yf_internal_retries", 0) or 0),
    )


def screen_one(ticker: str, settings: dict | None = None) -> Pick | None:
    settings = settings or load_settings()
    fetcher = _build_fetcher(settings)
    snap = fetcher.get(ticker)
    score = score_ticker(snap, settings)
    if score.error:
        return Pick(score=score, thesis=generate_thesis(score, snap.info), snap_info=snap.info)
    thesis = generate_thesis(score, snap.info)
    return Pick(score=score, thesis=thesis, snap_info=snap.info)


def _score_one(
    ticker: str, fetcher: Fetcher, settings: dict, *, force_refresh: bool
) -> Pick | None:
    """Fetch, score, generate thesis. Returns None on hard failure (logged)."""
    try:
        snap: TickerSnapshot = fetcher.get(ticker, force_refresh=force_refresh)
        ts = score_ticker(snap, settings)
        thesis = generate_thesis(ts, snap.info)
        return Pick(score=ts, thesis=thesis, snap_info=snap.info)
    except Exception as e:
        log.exception("Failed to score %s: %s", ticker, e)
        return None


def run_universe(
    settings: dict | None = None,
    *,
    limit: int | None = None,
    force_refresh: bool = False,
    sample: bool = False,
    max_workers: int | None = None,
    cache: Cache | None = None,
) -> list[Pick]:
    """Score the configured universe of tickers.

    Tickers are fetched/scored in parallel with a bounded pool. The pool size
    and a shared rate limiter (see `throttle` in settings) together keep the
    aggregate Yahoo request rate low enough to avoid 429s / blank responses.
    Cache hits return instantly, so warm runs are fast regardless of the limit.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    settings = settings or load_settings()
    if max_workers is None:
        max_workers = int(settings.get("throttle", {}).get("max_workers", 3) or 3)
    tickers = load_universe(settings)
    if limit:
        if sample:
            import random
            rnd = random.Random(0)  # deterministic across runs
            tickers = rnd.sample(tickers, min(limit, len(tickers)))
        else:
            tickers = tickers[:limit]
    else:
        # Shuffle the FULL universe (deterministically) so processing order isn't
        # alphabetical. Yahoo throttling or the deploy-time `timeout` can cut a run
        # short; with A→Z order a partial run yields only early-alphabet names (the
        # "website only shows A–C companies" symptom). A seeded shuffle makes any
        # partial run a representative A–Z sample, while a complete run is
        # unaffected (final output is ranked by score, not input order). Seed is
        # fixed so cache warming stays reproducible across runs.
        import random
        random.Random(1).shuffle(tickers)
    log.info("Screening %d tickers (max_workers=%d)", len(tickers), max_workers)

    fetcher = _build_fetcher(settings, cache=cache)
    picks: list[Pick] = []
    completed = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_score_one, t, fetcher, settings, force_refresh=force_refresh): t
            for t in tickers
        }
        for fut in as_completed(futures):
            completed += 1
            result = fut.result()
            if result is not None:
                picks.append(result)
            if completed % 100 == 0:
                log.info("Progress: %d/%d", completed, len(tickers))

    # Tier-2 enrichment (validate finalists + rescue gate-failures) needs the
    # ranking to pick finalists, so sort first, enrich, then re-sort by the
    # post-demotion effective score.
    picks.sort(key=lambda p: p.score.total, reverse=True)
    picks = enrich_picks(picks, settings, fetcher=fetcher)
    picks.sort(key=lambda p: p.score.effective_total, reverse=True)
    return picks


def enrich_picks(picks: list[Pick], settings: dict, *, fetcher: Fetcher) -> list[Pick]:
    """Tier-2: cross-source validate/gap-fill the picks that matter.

    Runs the secondary adapters (EDGAR/FMP/Finnhub) only on the top-N finalists
    plus completeness-gate failures, then re-scores each merged snapshot. Returns
    a new picks list with enriched picks substituted in place; on any config/key
    gap it is a no-op, so the universe sweep behaves exactly as before.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from .data.enrich import build_adapters, enrich_snapshot, _penalty_for
    from .data.merge import summarize_quality

    merge_cfg = (settings.get("sources", {}) or {}).get("merge", {}) or {}
    if not merge_cfg.get("enabled", True):
        return picks
    adapters = build_adapters(settings, fetcher.cache)
    if not adapters:
        return picks

    divergence = float(merge_cfg.get("divergence_pct_flag", 5.0))
    field_div = {k: float(v) for k, v in
                 (merge_cfg.get("divergence_pct_by_field", {}) or {}).items()}
    top_n = int(merge_cfg.get("enrich_top_n", 50))
    scorable = [p for p in picks if not p.score.error]
    targets: dict[str, Pick] = {p.score.ticker: p for p in scorable[:top_n]}
    if merge_cfg.get("rescue_gate_failures", True):
        rescue_limit = int(merge_cfg.get("rescue_limit", 150))
        rescued = [p for p in picks if p.score.error
                   and str(p.score.error).startswith("incomplete data")]
        for p in rescued[:rescue_limit]:
            targets.setdefault(p.score.ticker, p)
    if not targets:
        return picks
    log.info("Enriching %d picks via %s", len(targets),
             ", ".join(a.name for a in adapters))

    def _enrich_one(ticker: str) -> tuple[str, Pick]:
        snap = fetcher.get(ticker)  # warm cache hit
        merged, flags = enrich_snapshot(snap, adapters, divergence_pct=divergence,
                                        field_divergence=field_div)
        ts = score_ticker(merged, settings)
        ts.corroboration_penalty = _penalty_for(flags)
        thesis = generate_thesis(ts, merged.info)
        return ticker, Pick(score=ts, thesis=thesis, snap_info=merged.info,
                            provenance=merged.provenance, flags=merged.flags,
                            dq=summarize_quality(merged.provenance, flags))

    updated: dict[str, Pick] = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {pool.submit(_enrich_one, t): t for t in targets}
        for fut in as_completed(futs):
            try:
                t, newp = fut.result()
                updated[t] = newp
            except Exception as e:
                log.debug("enrich failed for %s: %s", futs[fut], e)
    return [updated.get(p.score.ticker, p) for p in picks]


def split_picks(
    picks: list[Pick], settings: dict
) -> tuple[list[Pick], list[Pick], list[Pick]]:
    """Bucket scored picks into (strong, angles, partial).

    Partial is the safety-net tier — guarantees the dashboard isn't empty even
    when no stocks clear the higher bars. Anything below partial_match is dropped.

    Data coverage gates tier eligibility: a high score built on only a couple of
    metrics is a statistical leap, not a strong match. Names with thin coverage
    are demoted (or dropped) so the top tiers only contain well-evidenced picks.
    """
    thr = settings["score_thresholds"]
    strong_thr = float(thr["strong_match"])
    angle_thr = float(thr["interesting_angle"])
    partial_thr = float(thr.get("partial_match", 45))
    cov_cfg = settings.get("coverage", {})
    surface_min = float(cov_cfg.get("min_surface", 0.40))
    strong_min = float(cov_cfg.get("strong_min", 0.65))
    angle_min = float(cov_cfg.get("angle_min", 0.55))
    strong, angles, partial = [], [], []
    for p in picks:
        if p.score.error:
            continue
        cov = getattr(p.score, "data_coverage", 1.0)
        if cov < surface_min:
            continue  # too little data to make any claim
        # effective_total applies the cross-source corroboration penalty, so an
        # uncorroborated finalist can drop a tier (annotate + demote policy).
        s = getattr(p.score, "effective_total", p.score.total)
        if s >= strong_thr and cov >= strong_min:
            strong.append(p)
        elif s >= angle_thr and cov >= angle_min:
            angles.append(p)
        elif s >= partial_thr:
            partial.append(p)
    limits = settings.get("surface_limits", {})
    strong = _limit_bucket(strong, limits.get("strong"))
    angles = _limit_bucket(angles, limits.get("angles"))
    partial = _limit_bucket(partial, limits.get("partial"))
    return strong, angles, partial


def _limit_bucket(picks: list[Pick], limit: int | None) -> list[Pick]:
    if limit is None:
        return picks
    limit = int(limit)
    if limit <= 0:
        return []
    return picks[:limit]


def gather_stock_news(picks: list[Pick], *, per_ticker: int = 3) -> dict[str, list[NewsItem]]:
    """Fetch recent news for each surfaced pick. Runs threaded for speed."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    out: dict[str, list[NewsItem]] = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(fetch_stock_news, p.score.ticker, limit=per_ticker): p
                   for p in picks}
        for fut in as_completed(futures):
            p = futures[fut]
            try:
                out[p.score.ticker] = fut.result()
            except Exception as e:
                log.debug("news fetch for %s failed: %s", p.score.ticker, e)
                out[p.score.ticker] = []
    return out


def write_csv(picks: list[Pick], out_path: Path) -> None:
    import csv
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "ticker", "name", "sector", "total", "effective_total",
            "moat", "strength", "consistency", "valuation", "cap_alloc",
            "data_coverage", "price", "fcf_yield_pct", "margin_of_safety_pct",
            "provenance", "data_flags", "error",
        ])
        for p in picks:
            s = p.score
            dim = {d.name: d.score for d in s.dimensions}
            w.writerow([
                s.ticker, s.name, s.sector, s.total,
                getattr(s, "effective_total", s.total),
                round(dim.get("Moat & Profitability", 0), 1),
                round(dim.get("Financial Strength", 0), 1),
                round(dim.get("Consistency", 0), 1),
                round(dim.get("Valuation / Margin of Safety", 0), 1),
                round(dim.get("Capital Allocation", 0), 1),
                getattr(s, "data_coverage", ""),
                s.valuation.price,
                s.valuation.fcf_yield_pct,
                s.valuation.margin_of_safety_pct,
                ";".join(f"{k}={v}" for k, v in (getattr(p, "provenance", {}) or {}).items()),
                " | ".join(getattr(p, "flags", []) or []),
                s.error or "",
            ])
