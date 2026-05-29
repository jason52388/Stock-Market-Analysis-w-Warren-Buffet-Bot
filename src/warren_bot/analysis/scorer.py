"""Score a ticker against Buffett/Munger criteria.

Each criterion maps an actual value to 0-100 via a piecewise linear function
defined by `target` (= 70) and `excellent` (= 100). Dimensions roll up to a
weighted total. Heat-map cells classify each criterion as hit/marginal/miss.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from ..data.fetcher import TickerSnapshot
from .growth import GrowthSet, compute_growth
from .ratios import RatioSet, compute_ratios
from .valuation import ValuationSet, compute_valuation

Status = Literal["hit", "marginal", "miss", "na"]


@dataclass
class HeatCell:
    criterion: str
    label: str
    value: float | None
    target: float
    excellent: float
    score: float            # 0-100
    status: Status
    unit: str = ""          # "%", "x", ""

    def display(self) -> str:
        if self.value is None:
            return "n/a"
        if self.unit == "%":
            return f"{self.value:.1f}%"
        if self.unit == "x":
            return f"{self.value:.2f}x"
        return f"{self.value:.2f}"


@dataclass
class DimensionScore:
    name: str
    score: float            # 0-100
    cells: list[HeatCell] = field(default_factory=list)


@dataclass
class TickerScore:
    ticker: str
    name: str
    sector: str
    total: float            # 0-100
    dimensions: list[DimensionScore]
    ratios: RatioSet
    growth: GrowthSet
    valuation: ValuationSet
    error: str | None = None

    def cells(self) -> list[HeatCell]:
        return [c for d in self.dimensions for c in d.cells]

    def hits(self) -> list[HeatCell]:
        return [c for c in self.cells() if c.status == "hit"]

    def misses(self) -> list[HeatCell]:
        return [c for c in self.cells() if c.status == "miss"]


def _piecewise_score(value: float | None, target: float, excellent: float,
                     lower_is_better: bool = False) -> float:
    """Map a raw value to 0-100.

    Higher-is-better: 0 -> 0, target -> 70, excellent -> 100, clipped above.
    Lower-is-better: mirror (e.g. D/E target 0.5, excellent 0.2).
    Missing -> 0 (neutral so it doesn't crash, but a 'na' cell is also emitted).
    """
    if value is None:
        return 0.0
    if lower_is_better:
        if value <= excellent:
            return 100.0
        if value <= target:
            # Between excellent and target, scale 100 -> 70
            span = target - excellent
            if span == 0:
                return 70.0
            return 70.0 + (target - value) / span * 30.0
        # Beyond target, decay toward 0. Use |target - excellent| as the
        # decay span so this works even when target == 0 (e.g. share_count_cagr).
        decay_span = abs(target - excellent) or 1.0
        return max(0.0, 70.0 * (1 - (value - target) / decay_span))
    else:
        if value >= excellent:
            return 100.0
        if value >= target:
            span = excellent - target
            if span == 0:
                return 70.0
            return 70.0 + (value - target) / span * 30.0
        # Below target: linear from 0 at <=0 (or below half-target) to 70 at target
        floor = 0.0 if target >= 0 else target * 2
        if value <= floor:
            return 0.0
        return max(0.0, (value - floor) / (target - floor) * 70.0)


def _status(score: float, value: float | None) -> Status:
    if value is None:
        return "na"
    if score >= 70:
        return "hit"
    if score >= 40:
        return "marginal"
    return "miss"


def _make_cell(crit: str, label: str, value: float | None, cfg: dict, unit: str = "") -> HeatCell:
    target = float(cfg["target"])
    excellent = float(cfg["excellent"])
    lib = bool(cfg.get("lower_is_better", False))
    score = _piecewise_score(value, target, excellent, lower_is_better=lib)
    return HeatCell(
        criterion=crit,
        label=label,
        value=value,
        target=target,
        excellent=excellent,
        score=score,
        status=_status(score, value),
        unit=unit,
    )


def score_ticker(snap: TickerSnapshot, settings: dict) -> TickerScore:
    crit_cfg = settings["criteria"]
    weights = settings["weights"]

    if not snap.ok:
        return TickerScore(
            ticker=snap.ticker,
            name=snap.info.get("shortName", snap.ticker) if snap.info else snap.ticker,
            sector=snap.info.get("sector", "") if snap.info else "",
            total=0.0,
            dimensions=[],
            ratios=compute_ratios(snap),
            growth=compute_growth(snap),
            valuation=compute_valuation(snap),
            error=snap.error or "missing financials",
        )

    ratios = compute_ratios(snap)
    growth = compute_growth(snap)
    valuation = compute_valuation(snap)

    moat = DimensionScore("Moat & Profitability", 0, [
        _make_cell("roe_pct", "ROE (10y avg)", ratios.roe_pct_avg, crit_cfg["roe_pct"], "%"),
        _make_cell("roic_pct", "ROIC (10y avg)", ratios.roic_pct_avg, crit_cfg["roic_pct"], "%"),
        _make_cell("gross_margin_pct", "Gross margin", ratios.gross_margin_pct_avg,
                   crit_cfg["gross_margin_pct"], "%"),
        _make_cell("net_margin_pct", "Net margin", ratios.net_margin_pct_avg,
                   crit_cfg["net_margin_pct"], "%"),
    ])

    strength = DimensionScore("Financial Strength", 0, [
        _make_cell("debt_to_equity", "Debt/Equity", ratios.debt_to_equity,
                   crit_cfg["debt_to_equity"], "x"),
        _make_cell("interest_coverage", "Interest coverage", ratios.interest_coverage,
                   crit_cfg["interest_coverage"], "x"),
        _make_cell("current_ratio", "Current ratio", ratios.current_ratio,
                   crit_cfg["current_ratio"], "x"),
    ])

    yrs = growth.years_in_window or 0
    # Scale "profitable years" target down if yfinance only returns ~4 yrs of statements.
    # Excellent = all years; target = all-but-one.
    yrs_cfg = {"target": max(1, yrs - 1), "excellent": max(1, yrs)} if yrs else \
        crit_cfg["years_profitable"]
    fcf_cfg = {"target": max(1, yrs - 1), "excellent": max(1, yrs)} if yrs else \
        crit_cfg["fcf_positive_years"]
    consistency = DimensionScore("Consistency", 0, [
        _make_cell("years_profitable", f"Profitable yrs (of {yrs or 10})",
                   float(growth.years_profitable) if yrs else None, yrs_cfg),
        _make_cell("revenue_cagr_pct", "Revenue CAGR", growth.revenue_cagr_pct,
                   crit_cfg["revenue_cagr_pct"], "%"),
        _make_cell("eps_cagr_pct", "EPS/NI CAGR", growth.eps_cagr_pct,
                   crit_cfg["eps_cagr_pct"], "%"),
        _make_cell("fcf_positive_years", f"FCF+ years (of {yrs or 10})",
                   float(growth.fcf_positive_years) if yrs else None, fcf_cfg),
    ])

    val_dim = DimensionScore("Valuation / Margin of Safety", 0, [
        _make_cell("fcf_yield_pct", "FCF yield", valuation.fcf_yield_pct,
                   crit_cfg["fcf_yield_pct"], "%"),
        _make_cell("pe_vs_median_ratio", "P/E vs 10y median",
                   valuation.pe_vs_median_ratio, crit_cfg["pe_vs_median_ratio"], "x"),
        _make_cell("margin_of_safety_pct", "Margin of safety (DCF)",
                   valuation.margin_of_safety_pct,
                   crit_cfg["margin_of_safety_pct"], "%"),
    ])

    cap_alloc = DimensionScore("Capital Allocation", 0, [
        _make_cell("shareholder_yield_pct", "Shareholder yield",
                   valuation.shareholder_yield_pct,
                   crit_cfg["shareholder_yield_pct"], "%"),
        _make_cell("share_count_cagr_pct", "Share count CAGR",
                   growth.share_count_cagr_pct,
                   crit_cfg["share_count_cagr_pct"], "%"),
    ])

    dims = [moat, strength, consistency, val_dim, cap_alloc]
    for d in dims:
        d.score = sum(c.score for c in d.cells) / len(d.cells) if d.cells else 0.0

    total = (
        weights["moat"] * moat.score
        + weights["strength"] * strength.score
        + weights["consistency"] * consistency.score
        + weights["valuation"] * val_dim.score
        + weights["capital_allocation"] * cap_alloc.score
    )

    return TickerScore(
        ticker=snap.ticker,
        name=snap.info.get("shortName", snap.ticker),
        sector=snap.info.get("sector", ""),
        total=round(total, 1),
        dimensions=dims,
        ratios=ratios,
        growth=growth,
        valuation=valuation,
    )
