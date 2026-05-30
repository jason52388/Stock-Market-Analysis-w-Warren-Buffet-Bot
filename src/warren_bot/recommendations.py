"""Cross-reference quant picks with hedge-fund flows to surface 'confluence' names.

A stock is "recommended" when it scores well on Buffett criteria *and* shows up
in dataroma's super-investor data — held by many, being added by many, or being
sold by many. The blended score nudges up for holdings + accumulation and down
for distribution, then we tag each name with a tier so the reader can scan
quickly: consensus, accumulating, caution, quant-only.

Ranking is by *confluence* first. The barometer is a name that clears the
Buffett screen AND carries a positive hedge-fund signal (consensus/accumulating);
those always rank above Buffett-only names (quant-only), which in turn rank above
names super-investors are net-selling (caution). The composite score only orders
names *within* the same signal group — see ``_TIER_PRIORITY``.
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


# Ranking priority groups — the reader's barometer is *confluence*. A name that
# clears the Buffett screen AND carries a positive hedge-fund signal (held by
# super-investors and/or being accumulated) outranks everything else, no matter
# how high a competing score is. A Buffett-only name (no hedge-fund footprint)
# always sits below any positive-signal name. Names super-investors are
# net-*selling* rank last: an active exit is a worse sign than no signal at all.
# These groups are the *primary* sort key; composite_score only breaks ties
# within a group.
_TIER_PRIORITY: dict[Tier, int] = {
    "consensus": 2,     # Buffett + held by super-investors -> top barometer
    "accumulating": 2,  # Buffett + being added last qtr    -> top barometer
    "quant-only": 1,    # Buffett only, no hedge-fund signal -> lower priority
    "caution": 0,       # hedge funds net-selling           -> ranked last
}


def build_recommendations(
    picks: list[Pick],
    hedge_views: dict[ViewKind, HedgeFundView],
    *,
    min_quant_score: float = 60.0,
    min_data_coverage: float = 0.55,
    only_overlap: bool = False,
) -> list[Recommendation]:
    """Build the recommendation list.

    `only_overlap=False` (default) includes strong quant picks that no
    super-investor is currently touching — they end up in the "quant-only"
    tier in the dashboard so the tab is never empty. Pass `only_overlap=True`
    to restrict to names that hit *both* the quant screen and at least one
    dataroma view.
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
        # Gate and rank on the *effective* score (raw quant minus the cross-source
        # corroboration penalty) so the Recommended tab agrees with split_picks /
        # the final ranking — an uncorroborated name that got demoted out of the
        # pick tiers shouldn't reappear at the top here.
        eff = getattr(p.score, "effective_total", p.score.total)
        if eff < min_quant_score:
            continue
        # Don't recommend a name whose score rests on too few data points — a
        # composite built on incomplete fundamentals is not a confident call.
        if getattr(p.score, "data_coverage", 1.0) < min_data_coverage:
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

        # Composite = quant base (effective, post-corroboration) + signal
        # bonuses/penalties.
        composite = eff
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

    # Primary key: signal group (confluence > Buffett-only > net-selling).
    # Secondary key: composite_score within the group. reverse=True applies to
    # both, so a higher priority group always wins regardless of composite, and
    # composite only orders names that share a group.
    recs.sort(key=lambda r: (_TIER_PRIORITY[r.tier], r.composite_score), reverse=True)
    return recs
