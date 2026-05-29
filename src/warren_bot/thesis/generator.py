"""Template-driven investment thesis. v1 is rule-based — swap for an LLM call later."""
from __future__ import annotations

from dataclasses import dataclass

from ..analysis.scorer import TickerScore


@dataclass
class Thesis:
    summary: str            # one-liner
    business: str
    moat_read: str
    financial_posture: str
    growth_track_record: str
    valuation_view: str
    watch_outs: str

    def as_markdown(self) -> str:
        sections = [
            f"**{self.summary}**",
            f"_Business._ {self.business}",
            f"_Moat read._ {self.moat_read}",
            f"_Financial posture._ {self.financial_posture}",
            f"_Growth track record._ {self.growth_track_record}",
            f"_Valuation view._ {self.valuation_view}",
            f"_Watch-outs._ {self.watch_outs}",
        ]
        return "\n\n".join(sections)


def _qual(score: float) -> str:
    if score >= 85:
        return "exceptional"
    if score >= 70:
        return "strong"
    if score >= 55:
        return "decent"
    if score >= 40:
        return "mixed"
    return "weak"


def _fmt(v: float | None, unit: str = "") -> str:
    if v is None:
        return "n/a"
    if unit == "%":
        return f"{v:.1f}%"
    if unit == "x":
        return f"{v:.2f}x"
    return f"{v:.2f}"


def _moat_read(ts: TickerScore) -> str:
    r = ts.ratios
    parts = []
    if r.roe_pct_avg is not None:
        parts.append(f"10yr avg ROE of {_fmt(r.roe_pct_avg, '%')}")
    if r.roic_pct_avg is not None:
        parts.append(f"ROIC {_fmt(r.roic_pct_avg, '%')}")
    if r.gross_margin_pct_avg is not None:
        parts.append(f"gross margin {_fmt(r.gross_margin_pct_avg, '%')}")
    metrics = ", ".join(parts) if parts else "limited margin data"
    qual = _qual(ts.dimensions[0].score)
    pricing_power = (
        "suggests real pricing power and a durable franchise"
        if r.gross_margin_pct_avg and r.gross_margin_pct_avg >= 40
        else "indicates a commodity-leaning economics profile"
    )
    return f"{qual.title()} moat signals: {metrics}. The combination {pricing_power}."


def _financial_posture(ts: TickerScore) -> str:
    r = ts.ratios
    de = _fmt(r.debt_to_equity, "x")
    icov = _fmt(r.interest_coverage, "x")
    cur = _fmt(r.current_ratio, "x")
    qual = _qual(ts.dimensions[1].score)
    return (
        f"{qual.title()} balance sheet — debt/equity {de}, interest coverage {icov}, "
        f"current ratio {cur}. Buffett gravitates to businesses that can survive any environment, "
        f"not just optimize for the next quarter."
    )


def _growth(ts: TickerScore) -> str:
    g = ts.growth
    profitable = f"{g.years_profitable}/{g.years_in_window} profitable years"
    fcf = f"{g.fcf_positive_years}/{g.years_in_window} FCF-positive years"
    rev = _fmt(g.revenue_cagr_pct, "%")
    eps = _fmt(g.eps_cagr_pct, "%")
    consistency = _qual(ts.dimensions[2].score)
    return (
        f"{consistency.title()} consistency: {profitable}, {fcf}, "
        f"revenue CAGR {rev}, EPS/NI CAGR {eps}. Steady earnings power is the "
        f"'inevitable' Buffett looks for over heroic growth."
    )


def _valuation_view(ts: TickerScore) -> str:
    v = ts.valuation
    parts = []
    if v.price is not None:
        parts.append(f"price ${_fmt(v.price)}")
    if v.fcf_yield_pct is not None:
        parts.append(f"FCF yield {_fmt(v.fcf_yield_pct, '%')}")
    if v.pe_ratio is not None:
        parts.append(f"P/E {_fmt(v.pe_ratio, 'x')}")
    if v.margin_of_safety_pct is not None:
        parts.append(f"MoS to DCF {_fmt(v.margin_of_safety_pct, '%')}")
    head = ", ".join(parts) if parts else "valuation data sparse"
    qual = _qual(ts.dimensions[3].score)
    mos = v.margin_of_safety_pct
    if mos is None:
        verdict = "DCF inconclusive — relying on yield and historical P/E"
    elif mos >= 30:
        verdict = "trades at a real discount to estimated intrinsic value"
    elif mos >= 0:
        verdict = "trades near fair value — limited margin of safety"
    else:
        verdict = "appears expensive vs estimated intrinsic value"
    return f"{qual.title()} valuation: {head}. The stock {verdict}."


def _watch_outs(ts: TickerScore) -> str:
    misses = ts.misses()
    if not misses:
        return "No major criteria miss — but always size positions for the unknowns you can't see."
    bullets = [f"{m.label} = {m.display()} (target {m.target}{m.unit})" for m in misses[:5]]
    return "Criteria missed: " + "; ".join(bullets) + "."


def _business_snapshot(ts: TickerScore, snap_info: dict) -> str:
    long_desc = snap_info.get("longBusinessSummary", "") or snap_info.get("longName", "")
    sector = ts.sector or snap_info.get("sector", "n/a")
    industry = snap_info.get("industry", "")
    one_liner = long_desc.split(".")[0].strip() if long_desc else "Business description unavailable."
    extra = f" Sector: {sector}" + (f", industry: {industry}" if industry else "") + "."
    return one_liner + "." + extra if one_liner and not one_liner.endswith(".") else one_liner + extra


def generate_thesis(ts: TickerScore, snap_info: dict | None = None) -> Thesis:
    snap_info = snap_info or {}

    # Errored scores have empty dimensions — return a minimal thesis so downstream
    # rendering doesn't crash. These never make it into the dashboard anyway.
    if ts.error or not ts.dimensions:
        return Thesis(
            summary=f"{ts.ticker} — {ts.name}: data unavailable ({ts.error or 'no dimensions'}).",
            business=_business_snapshot(ts, snap_info),
            moat_read="No financials available to assess moat.",
            financial_posture="No balance-sheet data.",
            growth_track_record="No multi-year statements available.",
            valuation_view="Valuation cannot be computed.",
            watch_outs=f"Data error: {ts.error or 'missing financials'}.",
        )

    qual = _qual(ts.total)
    if ts.total >= 75:
        bucket = "Strong match"
    elif ts.total >= 60:
        bucket = "Interesting angle"
    else:
        bucket = "Below threshold"

    summary = (
        f"{ts.ticker} — {ts.name}: {bucket} ({ts.total}/100). "
        f"{_qual(ts.dimensions[0].score).title()} moat, "
        f"{_qual(ts.dimensions[1].score)} balance sheet, "
        f"{_qual(ts.dimensions[3].score)} valuation."
    )

    return Thesis(
        summary=summary,
        business=_business_snapshot(ts, snap_info),
        moat_read=_moat_read(ts),
        financial_posture=_financial_posture(ts),
        growth_track_record=_growth(ts),
        valuation_view=_valuation_view(ts),
        watch_outs=_watch_outs(ts),
    )
