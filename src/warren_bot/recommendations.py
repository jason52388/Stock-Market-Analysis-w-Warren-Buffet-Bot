"""Cross-reference quant picks with hedge-fund flows to surface 'confluence' names.

A stock is "recommended" when it scores well on Buffett criteria *and* shows up
in dataroma's super-investor data — held by many, being added by many, or being
sold by many. The blended score nudges up for holdings + accumulation and down
for distribution, then we tag each name with a tier so the reader can scan
quickly: consensus, accumulating, caution, quant-only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .hedge_funds.dataroma import HedgeFundView, ViewKind
from .pipeline import Pick

Tier = Literal["consensus", "accumulating", "caution", "quant-only"]


@dataclass
class Recommendation:
    pick: Pick
    quant_score: float
    holdings_count: int = 0    # # of super-investors holding it
    holdings_rank: int = 0     # rank in dataroma top-50 holdings (0 = not in list)
    buys_count: int = 0
    buys_rank: int = 0
    sells_count: int = 0
    sells_rank: int = 0
    composite_score: float = 0.0
    tier: Tier = "quant-only"
    reasons: list[str] = field(default_factory=list)


def _rank_bonus(rank: int, total: int, max_bonus: float) -> float:
    """Linear bonus from `max_bonus` at rank 1 to 0 at rank `total`."""
    if rank <= 0 or total <= 0:
        return 0.0
    return max_bonus * (1 - (rank - 1) / total)


def _pick_tier(rec: Recommendation) -> Tier:
    """Classify based on which dataroma views the stock appears in."""
    has_holdings = rec.holdings_count > 0
    is_buying = rec.buys_count > 0
    is_selling = rec.sells_count > 0

    if is_buying and not is_selling:
        return "accumulating"
    if is_selling and rec.sells_count > rec.buys_count:
        return "caution"
    if has_holdings:
        return "consensus"
    return "quant-only"


def build_recommendations(
    picks: list[Pick],
    hedge_views: dict[ViewKind, HedgeFundView],
    *,
    min_quant_score: float = 60.0,
    only_overlap: bool = False,
) -> list[Recommendation]:
    """Build the recommendation list.

    `only_overlap=True` (default) restricts to names that hit *both* the quant
    screen and at least one dataroma view — the user's "overlapping findings"
    ask. Pass False to also include strong quant picks that no super-investor
    is touching.
    """
    holdings_view = hedge_views.get("holdings")
    buys_view = hedge_views.get("buys")
    sells_view = hedge_views.get("sells")

    holdings_total = len(holdings_view.rows) if holdings_view else 0
    buys_total = len(buys_view.rows) if buys_view else 0
    sells_total = len(sells_view.rows) if sells_view else 0

    def index(view: HedgeFundView | None) -> dict[str, tuple[int, int]]:
        if not view:
            return {}
        return {r.ticker: (r.rank, r.metric_value) for r in view.rows}

    holdings_idx = index(holdings_view)
    buys_idx = index(buys_view)
    sells_idx = index(sells_view)

    recs: list[Recommendation] = []
    for p in picks:
        if p.score.error:
            continue
        if p.score.total < min_quant_score:
            continue

        ticker = p.score.ticker
        h_rank, h_count = holdings_idx.get(ticker, (0, 0))
        b_rank, b_count = buys_idx.get(ticker, (0, 0))
        s_rank, s_count = sells_idx.get(ticker, (0, 0))

        in_any_hedge = bool(h_count or b_count or s_count)
        if only_overlap and not in_any_hedge:
            continue

        rec = Recommendation(
            pick=p,
            quant_score=p.score.total,
            holdings_count=h_count, holdings_rank=h_rank,
            buys_count=b_count, buys_rank=b_rank,
            sells_count=s_count, sells_rank=s_rank,
        )

        # Composite = quant base + signal bonuses/penalties.
        composite = p.score.total
        composite += _rank_bonus(h_rank, holdings_total, max_bonus=8.0)
        composite += _rank_bonus(b_rank, buys_total, max_bonus=10.0)
        composite -= _rank_bonus(s_rank, sells_total, max_bonus=8.0)
        rec.composite_score = round(composite, 1)

        # Human-readable reasons.
        rec.reasons.append(f"Buffett score {p.score.total:.0f}/100")
        if h_count:
            rec.reasons.append(
                f"Held by {h_count} super-investor{'s' if h_count != 1 else ''}"
                + (f" (#{h_rank} in dataroma)" if h_rank else "")
            )
        if b_count:
            rec.reasons.append(
                f"{b_count} super-investor{'s' if b_count != 1 else ''} added/initiated last qtr"
            )
        if s_count:
            rec.reasons.append(
                f"{s_count} super-investor{'s' if s_count != 1 else ''} trimmed/exited last qtr"
            )

        rec.tier = _pick_tier(rec)
        recs.append(rec)

    recs.sort(key=lambda r: r.composite_score, reverse=True)
    return recs
