"""Render the self-contained HTML dashboard (Picks + Briefing tabs, JS filters)."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Iterable

from jinja2 import Environment

from ..analysis.statement_utils import dividend_yield_pct
from ..hedge_funds.dataroma import HedgeFundView, ManagerPortfolio, ViewKind
from ..news.briefing import Briefing
from ..news.stock_news import NewsItem
from ..pipeline import Pick
from ..recommendations import Recommendation


def _full_description(p: Pick) -> str:
    info = p.snap_info or {}
    return (info.get("longBusinessSummary") or info.get("description") or "").strip()


def _round(v: Any, n: int) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):  # NaN / inf
        return None
    return round(f, n)


def build_cockpit_data(
    picks: list[Pick],
    recommendations: list[Recommendation] | None = None,
    stock_news: dict[str, list[NewsItem]] | None = None,
) -> list[dict[str, Any]]:
    """Per-ticker payload for the Cockpit tab: every KPI, dim score, thesis, news.

    Returns a list (preserves order) so the picker can default to the first
    (highest-scoring) ticker. Inlined as JSON in the dashboard; keep primitive.
    """
    stock_news = stock_news or {}
    rec_by_ticker: dict[str, Recommendation] = {}
    for r in recommendations or []:
        rec_by_ticker[r.pick.score.ticker] = r

    out: list[dict[str, Any]] = []
    for p in picks:
        if p.score.error:
            continue
        s = p.score
        info = p.snap_info or {}
        val = s.valuation
        rat = s.ratios

        # Normalize via the shared helper (rate-derived when possible) so the
        # dashboard, valuation, and scorer all agree on dividend yield.
        _dy = dividend_yield_pct(info)
        div_yld = _dy if _dy else None

        dims = [{"n": d.name, "s": _round(d.score, 0)} for d in s.dimensions]

        news_items = stock_news.get(s.ticker, [])[:5]
        news = [{
            "t": n.title,
            "u": n.url,
            "p": n.publisher,
            "d": n.published_at.strftime("%b %d") if n.published_at else "",
        } for n in news_items]

        rec = rec_by_ticker.get(s.ticker)
        out.append({
            "t": s.ticker,
            "n": s.name,
            "sc": s.sector or "",
            # Price block
            "px": _round(info.get("regularMarketPrice") or val.price, 2),
            "mc": info.get("marketCap"),
            "w52h": _round(info.get("fiftyTwoWeekHigh"), 2),
            "w52l": _round(info.get("fiftyTwoWeekLow"), 2),
            "beta": _round(info.get("beta"), 2),
            # Valuation
            "pe": _round(info.get("trailingPE"), 2),
            "fpe": _round(info.get("forwardPE"), 2),
            "pb": _round(info.get("priceToBook"), 2),
            "peg": _round(info.get("pegRatio") or info.get("trailingPegRatio"), 2),
            "ps": _round(info.get("priceToSalesTrailing12Months"), 2),
            "evEbitda": _round(info.get("enterpriseToEbitda"), 2),
            "mos": _round(val.margin_of_safety_pct, 0),
            "iv": _round(val.intrinsic_value_per_share, 2),
            # Cash returns
            "dy": _round(div_yld, 2),
            "fcy": _round(val.fcf_yield_pct, 2),
            "shy": _round(val.shareholder_yield_pct, 2),
            # Profitability / ratios
            "roe": _round(rat.roe_pct_avg, 1),
            "roic": _round(rat.roic_pct_avg, 1),
            "gm": _round(rat.gross_margin_pct_avg, 1),
            "nm": _round(rat.net_margin_pct_avg, 1),
            "om": _round(info["operatingMargins"] * 100, 1) if info.get("operatingMargins") is not None else None,
            # Balance sheet
            "de": _round(rat.debt_to_equity, 2),
            "cr": _round(rat.current_ratio, 2),
            "ic": _round(rat.interest_coverage, 1),
            # Scores
            "score": _round(s.total, 1),
            "dims": dims,
            "composite": _round(rec.composite_score, 1) if rec else None,
            "tier": rec.tier if rec else None,
            "holdings": rec.holdings_count if rec else 0,
            "buys": rec.buys_count if rec else 0,
            "sells": rec.sells_count if rec else 0,
            # Narrative
            "thesis": (p.thesis.summary or "").replace("**", "").replace("_", ""),
            "about": (info.get("longBusinessSummary") or info.get("description") or "")[:600],
            "news": news,
            # Analyst signals (yfinance)
            "recKey": info.get("recommendationKey"),
            "tgtMean": _round(info.get("targetMeanPrice"), 2),
            "tgtHigh": _round(info.get("targetHighPrice"), 2),
            "tgtLow": _round(info.get("targetLowPrice"), 2),
        })
    return out


def build_kpi_rows(picks: list[Pick]) -> list[dict[str, Any]]:
    """Flatten each Pick into a compact KPI dict for the Market KPIs tab.

    Inlined as JSON in the dashboard, so keep keys short and values primitive.
    Missing values become None (rendered as "—" client-side).
    """
    rows: list[dict[str, Any]] = []
    for p in picks:
        s = p.score
        info = p.snap_info or {}
        val = s.valuation
        rat = s.ratios

        # yfinance returns dividend yield as decimal (0.018) on newer versions
        # and as percent (1.8) on older ones — normalize to percent.
        # Normalize via the shared helper (rate-derived when possible) so the
        # dashboard, valuation, and scorer all agree on dividend yield.
        _dy = dividend_yield_pct(info)
        div_yld = _dy if _dy else None

        rows.append({
            "t": s.ticker,
            "n": s.name,
            "sc": s.sector or "",
            "px": _round(info.get("regularMarketPrice") or val.price, 2),
            "mc": info.get("marketCap"),
            "pe": _round(info.get("trailingPE"), 2),
            "fpe": _round(info.get("forwardPE"), 2),
            "pb": _round(info.get("priceToBook"), 2),
            "dy": _round(div_yld, 2),
            "fcy": _round(val.fcf_yield_pct, 2),
            "roe": _round(rat.roe_pct_avg, 1),
            "de": _round(rat.debt_to_equity, 2),
            "beta": _round(info.get("beta"), 2),
            "score": _round(s.total, 1),
        })
    return rows


_TEMPLATE = r"""
<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Buffett Bot — {{ date }}</title>
<style>
:root {
  --bg: #fafaf7; --card: #ffffff; --ink: #1a1a1a; --muted: #6a6a6a;
  --line: #e5e3dc; --hit: #1f8f4a; --hit-bg: #e7f4ec;
  --marg: #c69400; --marg-bg: #fdf5d8;
  --miss: #c0392b; --miss-bg: #fce8e6;
  --na: #999; --na-bg: #f3f3f3;
  --accent: #1a1a1a;
}
* { box-sizing: border-box; }
body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
       background: var(--bg); color: var(--ink); }
.wrap { max-width: 980px; margin: 0 auto; padding: 20px 18px 60px; }
header { display: flex; align-items: baseline; gap: 14px; flex-wrap: wrap; }
header h1 { margin: 0; font-size: 22px; }
header .sub { color: var(--muted); font-size: 13px; }

.tabs { display: flex; gap: 4px; margin: 18px 0 0; border-bottom: 2px solid var(--line); }
.tab-btn { background: none; border: none; padding: 10px 16px; font-size: 14px;
           font-weight: 600; color: var(--muted); cursor: pointer;
           border-bottom: 3px solid transparent; margin-bottom: -2px; }
.tab-btn.active { color: var(--ink); border-bottom-color: var(--accent); }
.tab { display: none; padding-top: 16px; }
.tab.active { display: block; }

/* Sub-tabs within a top-level tab (e.g. Picks → Picks / Berkshire Holdings) */
.sub-tab-bar { display: flex; gap: 2px; margin: 4px 0 0; border-bottom: 1px solid var(--line); }
.sub-tab-btn { background: none; border: none; padding: 9px 16px; font-size: 13.5px;
               font-weight: 600; color: var(--muted); cursor: pointer;
               border-bottom: 3px solid transparent; margin-bottom: -1px; }
.sub-tab-btn:hover { color: var(--ink); }
.sub-tab-btn.active { color: var(--ink); border-bottom-color: var(--accent); }
.sub-tab-panel { display: none; padding-top: 16px; }
.sub-tab-panel.active { display: block; }

.controls { background: var(--card); border: 1px solid var(--line); border-radius: 10px;
            padding: 12px 14px; margin-bottom: 14px;
            display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px 16px; }
.control label { display: block; font-size: 11px; color: var(--muted);
                 text-transform: uppercase; letter-spacing: .03em; margin-bottom: 4px; }
.control input[type="range"] { width: 100%; }
.control select, .control input[type="text"] { width: 100%; padding: 6px 8px; font-size: 13px;
                                                border: 1px solid var(--line); border-radius: 6px;
                                                background: #fff; }
.control .val { font-weight: 600; font-size: 13px; }

.section-head { margin: 22px 0 8px; font-size: 14px; color: var(--muted);
                text-transform: uppercase; letter-spacing: .04em; font-weight: 700;
                border-top: 1px solid var(--line); padding-top: 12px; }
.section-head .count { color: var(--ink); }

.pick { background: var(--card); border: 1px solid var(--line); border-radius: 12px;
        padding: 14px 16px; margin: 10px 0; }
.pick.hidden { display: none; }
.pick-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.ticker { font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
          font-weight: 700; font-size: 16px; }
.name { color: var(--muted); font-size: 14px; }
.score-pill { margin-left: auto; background: #f2f1ec; border-radius: 999px;
              padding: 4px 12px; font-weight: 700; font-size: 13px; }
.score-pill.strong { background: var(--hit-bg); color: var(--hit); }
.score-pill.angle { background: var(--marg-bg); color: var(--marg); }
.score-pill.partial { background: #efeee8; color: #6a6a6a; }
.facts { font-size: 12px; color: var(--muted); margin-top: 4px; }
.facts span { margin-right: 10px; }
.descr { font-size: 13px; line-height: 1.55; color: #333; margin-top: 10px;
         background: #f7f6f1; border-radius: 8px; padding: 9px 12px; }
.descr .more { color: var(--muted); cursor: pointer; font-weight: 600;
               font-size: 12px; margin-left: 4px; }
.descr .more:hover { color: var(--ink); }
.descr-full { display: none; }
.descr.expanded .descr-short { display: none; }
.descr.expanded .descr-full { display: inline; }

.dim-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 6px; margin-top: 10px; }
.dim { background: #f7f6f1; border-radius: 8px; padding: 8px 10px; }
.dim-head { display: flex; justify-content: space-between; font-size: 12px;
            font-weight: 600; margin-bottom: 6px; color: #444; }
.dim-head .ds { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.cells { display: grid; gap: 3px; }
.cell { display: grid; grid-template-columns: 1fr auto auto; gap: 6px;
        font-size: 11.5px; padding: 3px 6px; border-radius: 5px; align-items: center; }
.cell .lbl { color: #555; }
.cell .val { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.cell .tgt { color: #888; font-size: 10.5px; }
.cell.hit { background: var(--hit-bg); }
.cell.marginal { background: var(--marg-bg); }
.cell.miss { background: var(--miss-bg); }
.cell.na { background: var(--na-bg); color: var(--na); }

.thesis { font-size: 13px; line-height: 1.55; color: #222; }
.thesis em { font-weight: 600; font-style: normal; color: #555; }
.descr-body { font-size: 13px; line-height: 1.55; color: #333; }

.news-item { font-size: 12.5px; margin: 8px 0; padding-bottom: 8px;
             border-bottom: 1px solid var(--line); }
.news-item:last-child { border-bottom: none; padding-bottom: 0; }
.news-item a { color: var(--ink); text-decoration: none; font-weight: 600; }
.news-item a:hover { text-decoration: underline; }
.news-item .meta { color: var(--muted); font-size: 11.5px; margin: 2px 0; }
.news-item .sum { color: #444; }
.no-content { color: var(--muted); font-style: italic; font-size: 13px; padding: 8px 0; }

/* Inner tabs on each pick card */
.card-tabs { margin-top: 14px; border-top: 1px solid var(--line); }
.card-tab-bar { display: flex; gap: 2px; margin-top: 8px; }
.card-tab-btn { background: none; border: none; padding: 7px 12px;
                font-size: 12px; font-weight: 600; color: var(--muted);
                cursor: pointer; border-radius: 6px 6px 0 0;
                border-bottom: 2px solid transparent; }
.card-tab-btn:hover { color: var(--ink); }
.card-tab-btn.active { color: var(--ink); border-bottom-color: var(--accent);
                       background: #f7f6f1; }
.card-tab-content { display: none; padding: 12px 4px 2px; }
.card-tab-content.active { display: block; }

/* Briefing */
.brief-topic { background: var(--card); border: 1px solid var(--line); border-radius: 12px;
               padding: 14px 16px; margin: 12px 0; }
.brief-topic h3 { margin: 0 0 8px; font-size: 16px; }
.brief-topic .topic-meta { color: var(--muted); font-size: 12px; margin-bottom: 8px; }
.article { padding: 8px 0; border-top: 1px solid var(--line); }
.article:first-of-type { border-top: none; }
.article .title { font-weight: 600; font-size: 14px; }
.article .title a { color: var(--ink); text-decoration: none; }
.article .title a:hover { text-decoration: underline; }
.article .meta { color: var(--muted); font-size: 12px; margin: 2px 0; }
.article .tier-1 { color: #1a1a1a; font-weight: 600; }
.article .summary { font-size: 12.5px; color: #444; margin-top: 3px; }
.article.hidden { display: none; }

/* Recommendations */
.rec { background: var(--card); border: 1px solid var(--line); border-radius: 12px;
       padding: 14px 16px; margin: 10px 0; display: grid;
       grid-template-columns: auto 1fr auto; gap: 14px; align-items: start; }
.rec.hidden { display: none; }
.rec .rank { font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
             font-size: 22px; font-weight: 700; color: var(--muted); min-width: 40px; }
.rec-body .head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.rec-body .ticker { font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
                    font-weight: 700; font-size: 16px; }
.rec-body .name { color: var(--muted); font-size: 14px; }
.rec-body .sector { color: var(--muted); font-size: 12px; margin-top: 2px; }
.rec-reasons { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
.tag { display: inline-block; border-radius: 999px; padding: 3px 10px;
       font-size: 11.5px; font-weight: 600; background: #f0eee6; color: #444; }
.tag.quant { background: #eef1f7; color: #2b4a7a; }
.tag.hold { background: #e7f4ec; color: var(--hit); }
.tag.buy { background: #d8efe0; color: #0f6b34; }
.tag.sell { background: #fde7e9; color: var(--miss); }
.rec .composite { text-align: right; min-width: 90px; }
.rec .composite .num { font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
                       font-weight: 700; font-size: 20px; }
.rec .composite .lbl { font-size: 10.5px; color: var(--muted);
                       text-transform: uppercase; letter-spacing: .04em; }
.tier-badge { display: inline-block; border-radius: 5px; padding: 2px 8px;
              font-size: 10.5px; font-weight: 700; text-transform: uppercase;
              letter-spacing: .04em; margin-left: 6px; }
.tier-badge.consensus { background: #e7f4ec; color: var(--hit); }
.tier-badge.accumulating { background: #d8efe0; color: #0f6b34; }
.tier-badge.caution { background: #fdf5d8; color: var(--marg); }
.tier-badge.quant-only { background: #eef1f7; color: #2b4a7a; }
.rec-thesis { margin-top: 8px; font-size: 12.5px; color: #444; line-height: 1.5; }
.rec-thesis em { font-weight: 600; font-style: normal; color: #555; }

.howto { background: #fffdf5; border: 1px solid #ecdfb6; border-radius: 10px;
         padding: 0; margin: 14px 0; overflow: hidden; }
.howto > summary { cursor: pointer; padding: 12px 16px; font-weight: 600;
                   font-size: 13.5px; list-style: none; color: var(--ink);
                   display: flex; align-items: center; gap: 8px; }
.howto > summary::-webkit-details-marker { display: none; }
.howto > summary::before { content: '▸'; color: var(--muted);
                            transition: transform .15s; display: inline-block; }
.howto[open] > summary::before { transform: rotate(90deg); }
.howto-body { padding: 4px 16px 14px; font-size: 13px; line-height: 1.6; color: #333; }
.howto-body code { background: #fff5d6; padding: 1px 6px; border-radius: 4px;
                   font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
                   font-size: 12px; }
.howto-body table { width: 100%; border-collapse: collapse; margin: 8px 0; }
.howto-body th, .howto-body td { padding: 5px 8px; text-align: left;
                                   border-bottom: 1px solid #ecdfb6;
                                   font-size: 12.5px; }
.howto-body th { font-weight: 600; color: var(--muted); font-size: 11px;
                  text-transform: uppercase; letter-spacing: .04em; }
.howto-body .tier-badge { font-size: 10px; }

/* Hedge funds */
.hf-tab-bar { display: flex; gap: 2px; margin: 12px 0 0;
              border-bottom: 1px solid var(--line); }
.hf-tab-btn { background: none; border: none; padding: 10px 16px;
              font-size: 13.5px; font-weight: 600; color: var(--muted);
              cursor: pointer; border-bottom: 3px solid transparent;
              margin-bottom: -1px; }
.hf-tab-btn:hover { color: var(--ink); }
.hf-tab-btn.active { color: var(--ink); border-bottom-color: var(--accent); }
.hf-section { background: var(--card); border: 1px solid var(--line); border-radius: 12px;
              padding: 14px 16px; margin: 14px 0; display: none; }
.hf-section.active { display: block; }
.hf-section h3 { margin: 0 0 4px; font-size: 16px; }
.hf-section .sub { color: var(--muted); font-size: 12.5px; margin-bottom: 10px; }
table.hf { width: 100%; border-collapse: collapse; font-size: 13px; }
table.hf th, table.hf td { padding: 7px 10px; text-align: left;
                            border-bottom: 1px solid var(--line); }
table.hf th { font-size: 11px; color: var(--muted); text-transform: uppercase;
              letter-spacing: .04em; font-weight: 700; background: #faf8f3; }
table.hf td.num { text-align: right; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
table.hf td.ticker { font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
                     font-weight: 700; width: 80px; }
table.hf tr.hidden { display: none; }
.match-pill { display: inline-block; background: var(--hit-bg); color: var(--hit);
              border-radius: 999px; padding: 1px 8px; font-size: 11px; font-weight: 700;
              margin-left: 6px; vertical-align: middle; }
.attribution { font-size: 11.5px; color: var(--muted); margin-top: 12px; }
.attribution a { color: var(--muted); }

.empty { color: var(--muted); padding: 24px; text-align: center; font-style: italic; }
footer { color: var(--muted); font-size: 11.5px; margin-top: 30px; text-align: center; }

/* Berkshire panel */
.brk-summary { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
               gap: 12px; background: var(--card); border: 1px solid var(--line);
               border-radius: 12px; padding: 14px 16px; margin: 12px 0; }
.brk-summary .stat .lbl { font-size: 11px; color: var(--muted);
                          text-transform: uppercase; letter-spacing: .04em; }
.brk-summary .stat .val { font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
                          font-weight: 700; font-size: 18px; margin-top: 2px; }
.act-pill { display: inline-block; border-radius: 999px; padding: 2px 9px;
            font-size: 11px; font-weight: 700; }
.act-pill.buy { background: #d8efe0; color: #0f6b34; }
.act-pill.add { background: #e7f4ec; color: var(--hit); }
.act-pill.sell { background: #fde7e9; color: var(--miss); }
.act-pill.reduce { background: #fdf5d8; color: var(--marg); }
.act-pill.none { background: #f3f3f3; color: var(--muted); }
.pct-bar { display: inline-block; height: 8px; background: #e7f4ec; border-radius: 4px;
           vertical-align: middle; margin-left: 6px; }
.delta.up { color: var(--hit); }
.delta.down { color: var(--miss); }

/* KPI table */
.kpi-meta { font-size: 12.5px; color: var(--muted); margin: 8px 0 12px;
            line-height: 1.55; }
.kpi-meta strong { color: var(--ink); }
.kpi-meta code { background: #f0eee6; padding: 1px 5px; border-radius: 4px;
                 font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
                 font-size: 11.5px; color: #2b4a7a; }
.kpi-fallback td { padding: 14px 12px !important; background: #fffdf5;
                   border-left: 3px solid #d6c889 !important; font-size: 13px; }
.kpi-fallback a { color: #1a1a1a; font-weight: 600; }
.kpi-fallback .yf-ticker { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.kpi-wrap { background: var(--card); border: 1px solid var(--line); border-radius: 12px;
            padding: 8px 4px; overflow-x: auto; }
table.kpi { width: 100%; border-collapse: collapse; font-size: 12.5px; min-width: 1100px; }
table.kpi th, table.kpi td { padding: 6px 9px; text-align: left;
                              border-bottom: 1px solid var(--line); white-space: nowrap; }
table.kpi th { font-size: 10.5px; color: var(--muted); text-transform: uppercase;
               letter-spacing: .04em; font-weight: 700; background: #faf8f3;
               cursor: pointer; user-select: none; position: sticky; top: 0; }
table.kpi th .sort-ind { color: var(--ink); margin-left: 3px; font-size: 9px; }
table.kpi th:hover { background: #f1eee5; }
table.kpi td.num { text-align: right; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
table.kpi td.ticker { font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
                      font-weight: 700; }
table.kpi td.name { max-width: 220px; overflow: hidden; text-overflow: ellipsis; }
table.kpi td.na { color: var(--na); }
.kpi-pager { display: flex; align-items: center; justify-content: space-between;
             padding: 10px 4px; font-size: 12.5px; color: var(--muted); }
.kpi-pager button { background: #fff; border: 1px solid var(--line); border-radius: 6px;
                    padding: 5px 12px; font-size: 12px; cursor: pointer; }
.kpi-pager button:disabled { opacity: .4; cursor: default; }
.kpi-pager .page-info { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }

/* Wrap any wide table so it scrolls horizontally inside its card on small screens. */
.hf-section { overflow-x: auto; -webkit-overflow-scrolling: touch; }

/* ===================== COCKPIT ===================== */
.cockpit-header { display: grid; grid-template-columns: 1fr auto auto; gap: 12px;
                  align-items: center; background: var(--card); border: 1px solid var(--line);
                  border-radius: 12px; padding: 12px 16px; margin-bottom: 14px; }
.cockpit-header .picker { display: flex; align-items: center; gap: 8px; }
.cockpit-header .picker label { font-size: 11px; color: var(--muted);
                                 text-transform: uppercase; letter-spacing: .04em; }
.cockpit-header select { padding: 8px 10px; font-size: 14px; border: 1px solid var(--line);
                          border-radius: 6px; background: #fff; min-width: 280px;
                          font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.cockpit-header .ck-name { font-size: 14px; color: var(--muted); }
.cockpit-header .ck-sector { background: #f0eee6; border-radius: 999px;
                              padding: 4px 12px; font-size: 12px; color: #444; }
.cockpit-header .ck-score { background: var(--hit-bg); color: var(--hit);
                             border-radius: 999px; padding: 6px 14px;
                             font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
                             font-weight: 700; font-size: 14px; }
.cockpit-header .ck-score.angle { background: var(--marg-bg); color: var(--marg); }
.cockpit-header .ck-score.weak { background: #efeee8; color: #6a6a6a; }

/* Circular layout: 3x3 grid, chart in center, 8 KPI panels around it */
.cockpit-ring { display: grid; grid-template-columns: 1fr 1.4fr 1fr;
                grid-template-rows: 1fr 1.4fr 1fr; gap: 12px;
                min-height: 720px; margin-bottom: 18px; }
.cockpit-ring .panel { background: var(--card); border: 1px solid var(--line);
                       border-radius: 12px; padding: 12px 14px; position: relative;
                       display: flex; flex-direction: column; }
.cockpit-ring .panel .panel-title { font-size: 10.5px; color: var(--muted);
                                     text-transform: uppercase; letter-spacing: .06em;
                                     font-weight: 700; margin-bottom: 8px;
                                     display: flex; align-items: center; gap: 6px; }
.cockpit-ring .panel .panel-title .clock { background: var(--accent); color: #fff;
                                            border-radius: 999px; font-size: 9px;
                                            padding: 1px 6px; font-weight: 700; }
.cockpit-ring .panel .kpis { display: grid; gap: 6px; flex: 1; align-content: start; }
.ck-row { display: grid; grid-template-columns: 1fr auto; gap: 8px; align-items: baseline;
          padding: 4px 0; border-bottom: 1px dotted var(--line); }
.ck-row:last-child { border-bottom: none; }
.ck-row .lbl { font-size: 11.5px; color: #555; }
.ck-row .val { font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
               font-weight: 700; font-size: 13.5px; color: var(--ink); text-align: right; }
.ck-row .val.up { color: var(--hit); }
.ck-row .val.down { color: var(--miss); }
.ck-row .val.na { color: var(--na); font-weight: 400; }
.ck-row .val .sub { font-size: 10.5px; color: var(--muted); font-weight: 400;
                    display: block; margin-top: 1px; }

/* Center chart pod */
.cockpit-ring .chart-pod { background: var(--card); border: 2px solid var(--accent);
                           border-radius: 16px; padding: 8px; position: relative;
                           display: flex; flex-direction: column;
                           box-shadow: 0 2px 18px rgba(0,0,0,0.06); }
.cockpit-ring .chart-pod .ticker-badge { position: absolute; top: -14px; left: 50%;
                                          transform: translateX(-50%);
                                          background: var(--accent); color: #fff;
                                          border-radius: 999px; padding: 4px 16px;
                                          font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
                                          font-weight: 700; font-size: 13px; letter-spacing: .04em; }
.cockpit-ring .chart-pod .chart-mount { flex: 1; min-height: 360px; position: relative;
                                         border-radius: 10px; overflow: hidden;
                                         background: #fafaf7; }
.cockpit-ring .chart-pod .chart-mount iframe { border: 0; width: 100%; height: 100%; }
.cockpit-ring .chart-pod .chart-foot { font-size: 11px; color: var(--muted);
                                        text-align: center; margin-top: 6px; }
.cockpit-ring .chart-pod .chart-foot a { color: var(--ink); font-weight: 600; }

/* Grid placement: clock positions */
.cockpit-ring .p-12 { grid-column: 2; grid-row: 1; }
.cockpit-ring .p-130 { grid-column: 3; grid-row: 1; }
.cockpit-ring .p-3 { grid-column: 3; grid-row: 2; }
.cockpit-ring .p-430 { grid-column: 3; grid-row: 3; }
.cockpit-ring .p-6 { grid-column: 2; grid-row: 3; }
.cockpit-ring .p-730 { grid-column: 1; grid-row: 3; }
.cockpit-ring .p-9 { grid-column: 1; grid-row: 2; }
.cockpit-ring .p-1030 { grid-column: 1; grid-row: 1; }
.cockpit-ring .chart-pod { grid-column: 2; grid-row: 2; }

/* Dim bars (Quality panel) */
.dim-bar { display: grid; grid-template-columns: 1fr auto; gap: 6px; align-items: center;
           font-size: 11px; }
.dim-bar .bar-track { grid-column: 1 / -1; height: 6px; background: #efeee8;
                       border-radius: 3px; overflow: hidden; margin-top: 2px; }
.dim-bar .bar-fill { height: 100%; background: var(--hit); }
.dim-bar .bar-fill.mid { background: var(--marg); }
.dim-bar .bar-fill.low { background: var(--miss); }

/* Tier pill in cockpit header */
.ck-tier-pill { display: inline-block; border-radius: 5px; padding: 2px 8px;
                font-size: 10.5px; font-weight: 700; text-transform: uppercase;
                letter-spacing: .04em; margin-left: 4px; }

/* Lower panels: thesis / about / news */
.cockpit-lower { display: grid; grid-template-columns: 1.4fr 1fr; gap: 14px; }
.cockpit-lower .panel { background: var(--card); border: 1px solid var(--line);
                        border-radius: 12px; padding: 14px 16px; }
.cockpit-lower h3 { margin: 0 0 8px; font-size: 13px; color: var(--muted);
                    text-transform: uppercase; letter-spacing: .04em; font-weight: 700; }
.cockpit-lower .thesis-body { font-size: 13px; line-height: 1.55; color: #222; }
.cockpit-lower .about-body { font-size: 12.5px; line-height: 1.5; color: #444;
                              margin-top: 10px; padding-top: 10px;
                              border-top: 1px solid var(--line); }
.cockpit-lower .news-list .news-item { margin: 0; padding: 8px 0;
                                        border-bottom: 1px solid var(--line); }
.cockpit-empty { padding: 30px; text-align: center; color: var(--muted); font-style: italic; }

@media (max-width: 640px) {
  .wrap { padding: 14px 12px 40px; }
  header { gap: 8px; }
  header h1 { font-size: 19px; }
  header .sub { font-size: 12px; }

  /* Top tabs and inner sub-tabs: scroll horizontally instead of wrapping. */
  .tabs, .hf-tab-bar, .card-tab-bar, .sub-tab-bar {
    flex-wrap: nowrap; overflow-x: auto; -webkit-overflow-scrolling: touch;
    scrollbar-width: none;
  }
  .tabs::-webkit-scrollbar, .hf-tab-bar::-webkit-scrollbar,
  .card-tab-bar::-webkit-scrollbar, .sub-tab-bar::-webkit-scrollbar { display: none; }
  .tab-btn, .hf-tab-btn, .sub-tab-btn { padding: 10px 12px; font-size: 13px; white-space: nowrap; flex: 0 0 auto; }
  .card-tab-btn { white-space: nowrap; flex: 0 0 auto; }

  /* Filter controls: one column, tighter. */
  .controls { grid-template-columns: 1fr; gap: 10px; padding: 10px 12px; }

  /* Pick cards: per-dimension grid stacks. */
  .dim-grid { grid-template-columns: 1fr; }
  .pick-head { gap: 6px; }
  .score-pill { margin-left: 0; }

  /* Recommendation cards: collapse 3-col layout, move composite under rank. */
  .rec { grid-template-columns: auto 1fr; gap: 8px 12px; padding: 12px 14px; }
  .rec .rank { font-size: 18px; min-width: 28px; }
  .rec .composite { grid-column: 1 / -1; text-align: left;
                    display: flex; align-items: baseline; gap: 6px;
                    border-top: 1px dashed var(--line); padding-top: 8px; }
  .rec .composite .num { font-size: 18px; }
  .rec-body .head { gap: 6px; }

  /* Berkshire summary: 2 stats per row instead of 5. */
  .brk-summary { grid-template-columns: repeat(2, 1fr); gap: 10px; padding: 12px; }
  .brk-summary .stat .val { font-size: 16px; }

  /* Hedge / Berkshire tables: tighter cells, smaller font (scroll wrapper above
     handles overflow). */
  table.hf { font-size: 12px; }
  table.hf th, table.hf td { padding: 5px 7px; }

  /* KPI table pager wraps onto two lines if needed. */
  .kpi-pager { flex-wrap: wrap; gap: 8px; }

  /* News and article items: a hair more breathing room. */
  .brief-topic { padding: 12px 14px; }
  .brief-topic h3 { font-size: 15px; }

  /* Cockpit: collapse the ring to a stack on mobile. */
  .cockpit-header { grid-template-columns: 1fr; gap: 8px; }
  .cockpit-header select { min-width: 0; width: 100%; }
  .cockpit-ring { grid-template-columns: 1fr; grid-template-rows: auto;
                  min-height: 0; gap: 10px; }
  .cockpit-ring .p-12, .cockpit-ring .p-130, .cockpit-ring .p-3,
  .cockpit-ring .p-430, .cockpit-ring .p-6, .cockpit-ring .p-730,
  .cockpit-ring .p-9, .cockpit-ring .p-1030, .cockpit-ring .chart-pod {
    grid-column: 1; grid-row: auto;
  }
  .cockpit-ring .chart-pod .chart-mount { min-height: 280px; }
  .cockpit-lower { grid-template-columns: 1fr; }
}
</style>
</head>
<body><div class="wrap">

<header>
  <h1>Buffett Bot</h1>
  <span class="sub">{{ date }} · {{ strong|length }} strong · {{ angles|length }} angles
       · {{ partial|length }} partial · {{ briefing.total_articles }} briefing articles</span>
</header>

<div class="tabs">
  <button class="tab-btn active" data-target="recs">Recommended</button>
  <button class="tab-btn" data-target="picks">Buffett picks</button>
  <button class="tab-btn" data-target="hedge">Hedge Funds</button>
  {% if cockpit_data %}<button class="tab-btn" data-target="cockpit">Cockpit</button>{% endif %}
  {% if kpi_rows %}<button class="tab-btn" data-target="kpis">All Stocks</button>{% endif %}
  <button class="tab-btn" data-target="briefing">Weekly Briefing</button>
</div>

<!-- ===================== RECOMMENDATIONS TAB ===================== -->
<section id="recs" class="tab active">

  <details class="howto">
    <summary>How recommendations are calculated</summary>
    <div class="howto-body">
      <p>A ticker is recommended when it passes the Buffett quant screen
      (≥60/100) and either appears in dataroma's super-investor data <em>or</em>
      scores well enough on its own. Each ticker gets a <strong>composite
      score</strong>:</p>
      <p style="background: #fff5d6; padding: 10px 12px; border-radius: 6px;
                font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
                font-size: 12.5px; margin: 8px 0;">
composite = quant_score<br>
&nbsp;&nbsp;+ holdings_bonus&nbsp;&nbsp;&nbsp;&nbsp;<span style="color:#666">// 0–8, scaled by rank in dataroma top-50 holdings</span><br>
&nbsp;&nbsp;+ accumulation_bonus&nbsp;<span style="color:#666">// 0–10, scaled by rank in last-quarter buys</span><br>
&nbsp;&nbsp;− distribution_penalty <span style="color:#666">// 0–8, scaled by rank in last-quarter sells</span>
      </p>
      <p>Rank 1 in any dataroma view = full bonus/penalty; rank 50 = zero;
      anything below 50 doesn't count. The <strong>tier</strong> is a
      categorical label of <em>why</em> the ticker is recommended:</p>
      <table>
        <thead><tr><th>Tier</th><th>Meaning</th></tr></thead>
        <tbody>
          <tr><td><span class="tier-badge accumulating">accumulating</span></td>
              <td>Passed quant screen + more super-investors buying than selling last quarter.
              Smart-money momentum in your favor.</td></tr>
          <tr><td><span class="tier-badge consensus">consensus</span></td>
              <td>Passed quant screen + widely held by super-investors with no buy/sell skew.
              Steady-state ownership.</td></tr>
          <tr><td><span class="tier-badge caution">caution</span></td>
              <td>Passed quant screen <em>but</em> more super-investors are
              trimming than adding. Smart money may know something. Worth
              extra due diligence.</td></tr>
          <tr><td><span class="tier-badge quant-only">quant-only</span></td>
              <td>Passed quant screen with no dataroma signal at all. Either
              the super-investors aren't tracking it, or it's already a
              consensus name and they're holding steady.</td></tr>
        </tbody>
      </table>
      <p style="color: var(--muted); font-size: 11.5px;">
        Quant inputs from yfinance financials; flow inputs from
        <a href="https://www.dataroma.com/m/grid.php" target="_blank">dataroma.com</a>'s
        aggregated 13F filings (updated quarterly, ~45 days after quarter end).
      </p>
    </div>
  </details>

  <div class="controls">
    <div class="control">
      <label>Tier</label>
      <select id="rTier">
        <option value="">All tiers</option>
        <option value="consensus">Consensus (held by many)</option>
        <option value="accumulating">Smart-money accumulating</option>
        <option value="caution">Caution (smart-money exiting)</option>
        <option value="quant-only">Quant-only (no hedge fund signal)</option>
      </select>
    </div>
    <div class="control">
      <label>Sector</label>
      <select id="rSector"><option value="">All sectors</option>
        {% for s in rec_sectors %}<option value="{{ s }}">{{ s }}</option>{% endfor %}
      </select>
    </div>
    <div class="control">
      <label>Min composite score: <span class="val" id="rMinVal">0</span></label>
      <input type="range" id="rMin" min="0" max="120" value="0" />
    </div>
    <div class="control">
      <label>Search</label>
      <input type="text" id="rSearch" placeholder="ticker or name" />
    </div>
  </div>

  <div class="section-head">
    Recommended — <span class="count" id="recCount">{{ recommendations|length }}</span>
    <span style="color: var(--muted); font-weight: 400; font-size: 12px; text-transform: none;">
      · ranked by composite of Buffett quant score + hedge fund signal
    </span>
  </div>

  {% if recommendations %}
  {% for r in recommendations %}
  <div class="rec" data-tier="{{ r.tier }}"
       data-sector="{{ r.pick.score.sector }}"
       data-composite="{{ r.composite_score }}"
       data-search="{{ (r.pick.score.ticker + ' ' + r.pick.score.name)|lower }}">
    <div class="rank">#{{ loop.index }}</div>
    <div class="rec-body">
      <div class="head">
        <span class="ticker">{{ r.pick.score.ticker }}</span>
        <span class="name">{{ r.pick.score.name }}</span>
        <span class="tier-badge {{ r.tier }}">{{ r.tier }}</span>
      </div>
      <div class="sector">{{ r.pick.score.sector or 'Unknown sector' }}
        {% if r.pick.score.valuation.price %} · ${{ '%.2f'|format(r.pick.score.valuation.price) }}{% endif %}
        {% if r.pick.score.valuation.margin_of_safety_pct is not none %} · MoS {{ '%.0f'|format(r.pick.score.valuation.margin_of_safety_pct) }}%{% endif %}
      </div>
      <div class="rec-reasons">
        <span class="tag quant">Quant {{ '%.0f'|format(r.quant_score) }}/100</span>
        {% if r.holdings_count %}
        <span class="tag hold">Held by {{ r.holdings_count }} super-investors{% if r.holdings_rank %} (#{{ r.holdings_rank }}){% endif %}</span>
        {% endif %}
        {% if r.buys_count %}
        <span class="tag buy">{{ r.buys_count }} buying last qtr{% if r.buys_rank %} (#{{ r.buys_rank }}){% endif %}</span>
        {% endif %}
        {% if r.sells_count %}
        <span class="tag sell">{{ r.sells_count }} selling last qtr{% if r.sells_rank %} (#{{ r.sells_rank }}){% endif %}</span>
        {% endif %}
      </div>
      <div class="rec-thesis">
        {{ r.pick.thesis.summary | replace('**','') | replace('_','') | safe }}
      </div>
    </div>
    <div class="composite">
      <div class="num">{{ '%.0f'|format(r.composite_score) }}</div>
      <div class="lbl">composite</div>
    </div>
  </div>
  {% endfor %}
  {% else %}
  <div class="empty">No overlapping recommendations this run.
    Either no quant picks scored ≥60, or none of them appear in dataroma's tracked
    super-investor portfolios. Check the Stock Picks and Hedge Funds tabs separately.</div>
  {% endif %}

  <div class="attribution">
    Composite = Buffett quant score + (held-by-managers bonus) + (current-accumulation bonus) − (current-distribution penalty).
    Sources: yfinance financials · <a href="https://www.dataroma.com/m/grid.php" target="_blank">dataroma.com</a> 13F aggregates.
  </div>
</section>

<!-- ===================== COCKPIT TAB ===================== -->
{% if cockpit_data %}
<section id="cockpit" class="tab">

  <div class="cockpit-header">
    <div class="picker">
      <label for="ckPicker">Stock</label>
      <select id="ckPicker">
        {% for c in cockpit_data %}
        <option value="{{ c.t }}">{{ c.t }} — {{ c.n }}</option>
        {% endfor %}
      </select>
      <span class="ck-name" id="ckName"></span>
    </div>
    <span class="ck-sector" id="ckSector"></span>
    <span class="ck-score" id="ckScore"></span>
  </div>

  <div class="cockpit-ring">

    <!-- 12 o'clock: Price & size -->
    <div class="panel p-12">
      <div class="panel-title"><span class="clock">12</span> Price &amp; Size</div>
      <div class="kpis" id="ckPrice"></div>
    </div>

    <!-- 1:30: Valuation -->
    <div class="panel p-130">
      <div class="panel-title"><span class="clock">1:30</span> Valuation</div>
      <div class="kpis" id="ckValuation"></div>
    </div>

    <!-- 3 o'clock: Cash returns -->
    <div class="panel p-3">
      <div class="panel-title"><span class="clock">3</span> Cash Returns</div>
      <div class="kpis" id="ckCash"></div>
    </div>

    <!-- 4:30: Profitability -->
    <div class="panel p-430">
      <div class="panel-title"><span class="clock">4:30</span> Profitability</div>
      <div class="kpis" id="ckProfit"></div>
    </div>

    <!-- Center: chart pod -->
    <div class="chart-pod">
      <div class="ticker-badge" id="ckTickerBadge">—</div>
      <div class="chart-mount" id="ckChartMount"></div>
      <div class="chart-foot">
        12-month price · TradingView ·
        <a id="ckYahooLink" target="_blank">Yahoo Finance ↗</a>
      </div>
    </div>

    <!-- 6 o'clock: Buffett verdict -->
    <div class="panel p-6">
      <div class="panel-title"><span class="clock">6</span> Verdict</div>
      <div class="kpis" id="ckVerdict"></div>
    </div>

    <!-- 7:30: Balance sheet -->
    <div class="panel p-730">
      <div class="panel-title"><span class="clock">7:30</span> Balance Sheet</div>
      <div class="kpis" id="ckBalance"></div>
    </div>

    <!-- 9 o'clock: Quality dimensions -->
    <div class="panel p-9">
      <div class="panel-title"><span class="clock">9</span> Buffett Dimensions</div>
      <div class="kpis" id="ckDims"></div>
    </div>

    <!-- 10:30: Risk -->
    <div class="panel p-1030">
      <div class="panel-title"><span class="clock">10:30</span> Risk &amp; Range</div>
      <div class="kpis" id="ckRisk"></div>
    </div>

  </div>

  <div class="cockpit-lower">
    <div class="panel">
      <h3>Thesis</h3>
      <div class="thesis-body" id="ckThesis"></div>
      <div class="about-body" id="ckAbout"></div>
    </div>
    <div class="panel">
      <h3>Recent News</h3>
      <div class="news-list" id="ckNews"></div>
    </div>
  </div>

  <div class="attribution">
    Chart: TradingView · Fundamentals: yfinance · Buffett score: this bot's
    composite (0–100) · Composite: quant + smart-money signal.
  </div>
</section>
<script id="ckData" type="application/json">{{ cockpit_json }}</script>
{% endif %}

<!-- ===================== PICKS TAB ===================== -->
<section id="picks" class="tab">

  <div class="sub-tab-bar">
    <button class="sub-tab-btn active" data-subtab="picks-list">Picks</button>
    {% if brk %}<button class="sub-tab-btn" data-subtab="berkshire-holdings">Berkshire Holdings</button>{% endif %}
  </div>

  <div class="sub-tab-panel active" data-subtab="picks-list">

  <div class="controls">
    <div class="control">
      <label>Min total score: <span class="val" id="vMin">0</span></label>
      <input type="range" id="fMinScore" min="0" max="100" value="0" />
    </div>
    <div class="control">
      <label>Bucket</label>
      <select id="fBucket">
        <option value="">All</option>
        <option value="strong">Strong matches only (≥75)</option>
        <option value="angle">Interesting angles only (60–74)</option>
        <option value="partial">Partial matches only (45–59)</option>
      </select>
    </div>
    <div class="control">
      <label>Sector</label>
      <select id="fSector"><option value="">All sectors</option>
        {% for s in sectors %}<option value="{{ s }}">{{ s }}</option>{% endfor %}
      </select>
    </div>
    <div class="control">
      <label>Strong moat? (≥70)</label>
      <select id="fMoat">
        <option value="">Any</option><option value="1">Yes</option>
      </select>
    </div>
    <div class="control">
      <label>Strong balance sheet? (≥70)</label>
      <select id="fStrength">
        <option value="">Any</option><option value="1">Yes</option>
      </select>
    </div>
    <div class="control">
      <label>Cheap on valuation? (≥70)</label>
      <select id="fValuation">
        <option value="">Any</option><option value="1">Yes</option>
      </select>
    </div>
    <div class="control">
      <label>Search ticker / name</label>
      <input type="text" id="fSearch" placeholder="e.g. KO, Costco" />
    </div>
  </div>

  <div class="section-head" id="strongHead">
    Strong matches — <span class="count" id="strongCount">{{ strong|length }}</span>
  </div>
  <div id="strongList">
    {% for p in strong %}{{ pick_card(p, "strong") }}{% endfor %}
    {% if not strong %}<div class="empty">No strong matches this run.</div>{% endif %}
  </div>

  <div class="section-head" id="angleHead">
    Interesting angles — <span class="count" id="angleCount">{{ angles|length }}</span>
    <span style="color: var(--muted); font-weight: 400; font-size: 12px; text-transform: none;">
      · score 60–74 · strong on some dimensions
    </span>
  </div>
  <div id="angleList">
    {% for p in angles %}{{ pick_card(p, "angle") }}{% endfor %}
    {% if not angles %}<div class="empty">No interesting angles this run.</div>{% endif %}
  </div>

  <div class="section-head" id="partialHead">
    Partial matches — <span class="count" id="partialCount">{{ partial|length }}</span>
    <span style="color: var(--muted); font-weight: 400; font-size: 12px; text-transform: none;">
      · score 45–59 · weaker fit, worth a glance
    </span>
  </div>
  <div id="partialList">
    {% for p in partial %}{{ pick_card(p, "partial") }}{% endfor %}
    {% if not partial %}<div class="empty">No partial matches this run.</div>{% endif %}
  </div>

  </div><!-- /picks-list panel -->

  <!-- ===================== BERKSHIRE HOLDINGS SUB-PANEL ===================== -->
  {% if brk %}
  <div class="sub-tab-panel" data-subtab="berkshire-holdings" id="berkshire">
  <div class="brk-summary">
    <div class="stat"><div class="lbl">Manager</div>
      <div class="val" style="font-size: 14px;">{{ brk.manager_name }}</div></div>
    <div class="stat"><div class="lbl">Period</div>
      <div class="val" style="font-size: 14px;">{{ brk.period }}</div></div>
    <div class="stat"><div class="lbl">As of</div>
      <div class="val" style="font-size: 14px;">{{ brk.portfolio_date }}</div></div>
    <div class="stat"><div class="lbl">Positions</div>
      <div class="val">{{ brk.positions|length }}</div></div>
    <div class="stat"><div class="lbl">Portfolio value</div>
      <div class="val">${{ '{:,.1f}B'.format(brk.portfolio_value_usd / 1e9) if brk.portfolio_value_usd else '—' }}</div></div>
  </div>

  <div class="controls">
    <div class="control">
      <label>Activity</label>
      <select id="brkAct">
        <option value="">All positions</option>
        <option value="buy">New buys only</option>
        <option value="add">Adds only</option>
        <option value="sell">Sells only</option>
        <option value="reduce">Reductions only</option>
        <option value="any">Any change (buy/add/sell/reduce)</option>
      </select>
    </div>
    <div class="control">
      <label>Search ticker / name</label>
      <input type="text" id="brkSearch" placeholder="e.g. AAPL" />
    </div>
    <div class="control">
      <label>Overlap with my picks</label>
      <select id="brkOverlap">
        <option value="">All</option>
        <option value="1">Only stocks that also passed my screen</option>
      </select>
    </div>
  </div>

  <div class="section-head">
    Holdings — <span class="count" id="brkCount">{{ brk.positions|length }}</span>
    <span style="color: var(--muted); font-weight: 400; font-size: 12px; text-transform: none;">
      · sorted by % of portfolio
    </span>
  </div>

  <div class="kpi-wrap">
    <table class="hf">
      <thead><tr>
        <th>#</th><th>Ticker</th><th>Company</th>
        <th class="num">% portfolio</th>
        <th class="num">Shares</th>
        <th class="num">Reported $</th>
        <th class="num">Value</th>
        <th class="num">Current $</th>
        <th class="num">+/- Reported</th>
        <th>Recent activity</th>
      </tr></thead>
      <tbody>
        {% for pos in brk.positions %}
        <tr data-act="{{ pos.activity_kind }}"
            data-ticker="{{ pos.ticker }}"
            data-search="{{ (pos.ticker + ' ' + pos.name)|lower }}"
            data-overlap="{{ '1' if pos.ticker in surfaced_tickers else '0' }}">
          <td class="num">{{ pos.rank }}</td>
          <td class="ticker">{{ pos.ticker }}{% if pos.ticker in surfaced_tickers %}<span class="match-pill">picked</span>{% endif %}</td>
          <td>{{ pos.name }}</td>
          <td class="num">{{ '%.2f'|format(pos.portfolio_pct) }}%
            <span class="pct-bar" style="width: {{ (pos.portfolio_pct * 4)|round(0,'floor')|int }}px;"></span>
          </td>
          <td class="num">{% if pos.shares %}{{ '{:,}'.format(pos.shares) }}{% else %}—{% endif %}</td>
          <td class="num">{% if pos.reported_price %}${{ '%.2f'|format(pos.reported_price) }}{% else %}—{% endif %}</td>
          <td class="num">{% if pos.value_usd %}${{ '{:,.1f}M'.format(pos.value_usd / 1e6) }}{% else %}—{% endif %}</td>
          <td class="num">{% if pos.current_price %}${{ '%.2f'|format(pos.current_price) }}{% else %}—{% endif %}</td>
          <td class="num">
            {% if pos.price_change_pct is not none %}
              <span class="delta {{ 'up' if pos.price_change_pct >= 0 else 'down' }}">
                {{ '%+.2f'|format(pos.price_change_pct) }}%
              </span>
            {% else %}—{% endif %}
          </td>
          <td>
            {% if pos.activity_kind != 'none' %}
              <span class="act-pill {{ pos.activity_kind }}">{{ pos.activity }}</span>
            {% else %}<span style="color: var(--muted);">—</span>{% endif %}
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>

  <div class="attribution">
    Source: <a href="{{ brk_url }}" target="_blank">dataroma.com/m/holdings.php?m=BRK</a> ·
    From 13F filings, updated ~45 days after each quarter end.
  </div>
  </div><!-- /berkshire-holdings panel -->
  {% endif %}

</section>

<!-- ===================== BRIEFING TAB ===================== -->
<section id="briefing" class="tab">

  <div class="controls">
    <div class="control">
      <label>Topic</label>
      <select id="bTopic"><option value="">All topics</option>
        {% for t, _ in briefing.topic_list %}<option value="{{ t }}">{{ t }}</option>{% endfor %}
      </select>
    </div>
    <div class="control">
      <label>Source</label>
      <select id="bSource"><option value="">All sources</option>
        {% for s in briefing_sources %}<option value="{{ s }}">{{ s }}</option>{% endfor %}
      </select>
    </div>
    <div class="control">
      <label>Tier-1 sources only</label>
      <select id="bTier1">
        <option value="">No</option><option value="1">Yes</option>
      </select>
    </div>
    <div class="control">
      <label>Search</label>
      <input type="text" id="bSearch" placeholder="keyword" />
    </div>
  </div>

  {% for topic, items in briefing.topic_list %}
  <div class="brief-topic" data-topic="{{ topic }}">
    <h3>{{ topic }}</h3>
    <div class="topic-meta">{{ items|length }} articles · past 7 days</div>
    {% for a in items %}
    <div class="article" data-topic="{{ topic }}" data-source="{{ a.source }}"
         data-tier="{{ a.tier }}"
         data-search="{{ (a.title + ' ' + a.summary)|lower }}">
      <div class="title"><a href="{{ a.url }}" target="_blank">{{ a.title }}</a></div>
      <div class="meta">
        <span class="tier-{{ a.tier }}">{{ a.source }}</span>
        {% if a.display_date %} · {{ a.display_date }}{% endif %}
      </div>
      {% if a.summary %}<div class="summary">{{ a.summary }}</div>{% endif %}
    </div>
    {% endfor %}
  </div>
  {% endfor %}
  {% if not briefing.topic_list %}
  <div class="empty">No briefing articles fetched this run. Check network or RSS feeds.</div>
  {% endif %}

</section>

<!-- ===================== HEDGE FUNDS TAB ===================== -->
<section id="hedge" class="tab">

  <div class="controls">
    <div class="control">
      <label>Sector</label>
      <select id="hSector"><option value="">All sectors</option>
        {% for s in hedge_sectors %}<option value="{{ s }}">{{ s }}</option>{% endfor %}
      </select>
    </div>
    <div class="control">
      <label>Search ticker / name</label>
      <input type="text" id="hSearch" placeholder="e.g. AAPL" />
    </div>
    <div class="control">
      <label>Overlap with my picks</label>
      <select id="hOverlap">
        <option value="">All</option>
        <option value="1">Only stocks that also passed Buffett screen</option>
      </select>
    </div>
  </div>

  {% set hf_labels = {'holdings': 'Largest holdings', 'buys': 'Biggest accumulation',
                       'sells': 'Biggest distribution'} %}
  <div class="hf-tab-bar">
    {% for kind, view in hedge_views.items() if view.rows %}
    <button class="hf-tab-btn {{ 'active' if loop.first }}" data-hftab="{{ kind }}">
      {{ hf_labels.get(kind, kind|title) }}
      <span style="color: var(--muted); font-weight: 400; margin-left: 4px;">({{ view.rows|length }})</span>
    </button>
    {% endfor %}
    {% if brk and brk.active_positions %}
    <button class="hf-tab-btn" data-hftab="brk-activity">
      Berkshire activity
      <span style="color: var(--muted); font-weight: 400; margin-left: 4px;">({{ brk.active_positions|length }})</span>
    </button>
    {% endif %}
  </div>

  {% for kind, view in hedge_views.items() %}
  {% if view.rows %}
  <div class="hf-section {{ 'active' if loop.first }}" data-hftab="{{ kind }}">
    <h3>{{ view.title }}</h3>
    <div class="sub">{{ view.subtitle }}</div>
    <table class="hf">
      <thead><tr>
        <th>#</th><th>Ticker</th><th>Company</th><th>Sector</th>
        <th class="num">{{ view.rows[0].metric_label or 'Metric' }}</th>
        <th class="num">Hold price</th>
      </tr></thead>
      <tbody>
        {% for r in view.rows %}
        <tr data-sector="{{ r.sector }}"
            data-ticker="{{ r.ticker }}"
            data-search="{{ (r.ticker + ' ' + r.name)|lower }}"
            data-overlap="{{ '1' if r.ticker in surfaced_tickers else '0' }}">
          <td class="num">{{ r.rank }}</td>
          <td class="ticker">{{ r.ticker }}{% if r.ticker in surfaced_tickers %}<span class="match-pill">picked</span>{% endif %}</td>
          <td>{{ r.name }}</td>
          <td>{{ r.sector }}</td>
          <td class="num">{{ r.metric_value }}</td>
          <td class="num">{% if r.hold_price %}${{ '%.2f'|format(r.hold_price) }}{% endif %}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
  {% endif %}
  {% endfor %}

  {% if brk and brk.active_positions %}
  <div class="hf-section" data-hftab="brk-activity">
    <h3>Berkshire Hathaway — recent activity</h3>
    <div class="sub">Buffett's latest quarter changes ({{ brk.period }}, reported {{ brk.portfolio_date }}).
      Buys/adds shown in green; sells/reductions in red.</div>
    <table class="hf">
      <thead><tr>
        <th>#</th><th>Ticker</th><th>Company</th>
        <th>Action</th>
        <th class="num">% portfolio</th>
        <th class="num">Reported $</th>
        <th class="num">+/- vs reported</th>
      </tr></thead>
      <tbody>
        {% for pos in brk.active_positions %}
        <tr data-ticker="{{ pos.ticker }}"
            data-search="{{ (pos.ticker + ' ' + pos.name)|lower }}"
            data-overlap="{{ '1' if pos.ticker in surfaced_tickers else '0' }}"
            data-sector="">
          <td class="num">{{ pos.rank }}</td>
          <td class="ticker">{{ pos.ticker }}{% if pos.ticker in surfaced_tickers %}<span class="match-pill">picked</span>{% endif %}</td>
          <td>{{ pos.name }}</td>
          <td><span class="act-pill {{ pos.activity_kind }}">{{ pos.activity }}</span></td>
          <td class="num">{{ '%.2f'|format(pos.portfolio_pct) }}%</td>
          <td class="num">{% if pos.reported_price %}${{ '%.2f'|format(pos.reported_price) }}{% else %}—{% endif %}</td>
          <td class="num">
            {% if pos.price_change_pct is not none %}
              <span class="delta {{ 'up' if pos.price_change_pct >= 0 else 'down' }}">
                {{ '%+.2f'|format(pos.price_change_pct) }}%
              </span>
            {% else %}—{% endif %}
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
  {% endif %}

  {% if not hedge_views or not any_hedge_rows %}
  <div class="empty">Hedge-fund data unavailable this run (dataroma fetch failed or disabled).</div>
  {% endif %}

  <div class="attribution">
    Source: <a href="https://www.dataroma.com/m/grid.php" target="_blank">dataroma.com</a> ·
    aggregated 13F filings from tracked super-investors · updated quarterly.
  </div>

</section>

<!-- ===================== MARKET KPIs TAB ===================== -->
{% if kpi_rows %}
<section id="kpis" class="tab">
  <div class="kpi-meta">
    <strong>Top stocks by market cap</strong> — {{ kpi_rows|length }} tickers
    screened (US + international). Default sort is descending market cap; click
    any column header to re-sort. Search accepts any US ticker (e.g.
    <code>NVDA</code>) or international ticker with exchange suffix (e.g.
    <code>ASML.AS</code>, <code>7203.T</code>, <code>SHEL.L</code>) — if it's
    not in our universe, we link straight to Yahoo Finance. "—" = not reported.
  </div>
  <div class="controls">
    <div class="control">
      <label>Sector</label>
      <select id="kSector"><option value="">All sectors</option>
        {% for s in kpi_sectors %}<option value="{{ s }}">{{ s }}</option>{% endfor %}
      </select>
    </div>
    <div class="control">
      <label>Search ticker / name</label>
      <input type="text" id="kSearch" placeholder="e.g. AAPL" />
    </div>
    <div class="control">
      <label>Min market cap</label>
      <select id="kMcap">
        <option value="0">Any</option>
        <option value="300000000">≥ $300M</option>
        <option value="2000000000">≥ $2B</option>
        <option value="10000000000">≥ $10B</option>
        <option value="50000000000">≥ $50B</option>
        <option value="200000000000">≥ $200B</option>
      </select>
    </div>
    <div class="control">
      <label>Max trailing P/E</label>
      <select id="kPe">
        <option value="">Any</option>
        <option value="10">≤ 10</option>
        <option value="15">≤ 15</option>
        <option value="20">≤ 20</option>
        <option value="30">≤ 30</option>
      </select>
    </div>
    <div class="control">
      <label>Min dividend yield</label>
      <select id="kDy">
        <option value="">Any</option>
        <option value="1">≥ 1%</option>
        <option value="2">≥ 2%</option>
        <option value="3">≥ 3%</option>
        <option value="5">≥ 5%</option>
      </select>
    </div>
  </div>

  <div class="kpi-wrap">
    <table class="kpi" id="kpiTable">
      <thead><tr>
        <th data-sort="t" data-num="0">Ticker</th>
        <th data-sort="n" data-num="0">Name</th>
        <th data-sort="sc" data-num="0">Sector</th>
        <th data-sort="px" data-num="1" class="num">Price</th>
        <th data-sort="mc" data-num="1" class="num">Market Cap</th>
        <th data-sort="pe" data-num="1" class="num">P/E</th>
        <th data-sort="fpe" data-num="1" class="num">Fwd P/E</th>
        <th data-sort="pb" data-num="1" class="num">P/B</th>
        <th data-sort="dy" data-num="1" class="num">Div Yld</th>
        <th data-sort="fcy" data-num="1" class="num">FCF Yld</th>
        <th data-sort="roe" data-num="1" class="num">ROE 10y</th>
        <th data-sort="de" data-num="1" class="num">D/E</th>
        <th data-sort="beta" data-num="1" class="num">Beta</th>
        <th data-sort="score" data-num="1" class="num">Buffett</th>
      </tr></thead>
      <tbody id="kpiBody"></tbody>
    </table>
  </div>
  <div class="kpi-pager">
    <div><span id="kpiTotal">0</span> rows match</div>
    <div>
      <button id="kpiPrev">← Prev</button>
      <span class="page-info" id="kpiPageInfo">page 1</span>
      <button id="kpiNext">Next →</button>
    </div>
  </div>

  <div class="attribution">
    Data: yfinance · KPIs reflect the most recent fetch (cached up to {{ cache_ttl_hours }}h).
    "Buffett" is this bot's composite score (0–100).
  </div>
</section>
<script id="kpiData" type="application/json">{{ kpi_json }}</script>
{% endif %}

<footer>
  Generated {{ generated_at }} · Quantitative proxies, not advice · DYOR
</footer>
</div>

<script>
(function() {
  // Top-level tabs
  document.querySelectorAll('.tab-btn').forEach(b => {
    b.addEventListener('click', () => {
      document.querySelectorAll('.tab-btn').forEach(x => x.classList.remove('active'));
      document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
      b.classList.add('active');
      document.getElementById(b.dataset.target).classList.add('active');
    });
  });

  // Sub-tabs within a top-level tab (Picks → Picks / Berkshire Holdings).
  // Scoped per-parent so multiple sub-tab groups don't interfere.
  document.querySelectorAll('.sub-tab-btn').forEach(b => {
    b.addEventListener('click', () => {
      const parent = b.closest('.tab');
      if (!parent) return;
      const key = b.dataset.subtab;
      parent.querySelectorAll('.sub-tab-btn').forEach(x => x.classList.remove('active'));
      parent.querySelectorAll('.sub-tab-panel').forEach(x => x.classList.remove('active'));
      b.classList.add('active');
      const panel = parent.querySelector('.sub-tab-panel[data-subtab="' + key + '"]');
      if (panel) panel.classList.add('active');
    });
  });

  // Per-card inner tabs (About / Buffett Analysis / News) via event delegation
  document.addEventListener('click', e => {
    const btn = e.target.closest('.card-tab-btn');
    if (!btn) return;
    const card = btn.closest('.pick');
    if (!card) return;
    const target = btn.dataset.tab;
    card.querySelectorAll('.card-tab-btn').forEach(b => b.classList.remove('active'));
    card.querySelectorAll('.card-tab-content').forEach(c => c.classList.remove('active'));
    btn.classList.add('active');
    const content = card.querySelector('.card-tab-content[data-tab="' + target + '"]');
    if (content) content.classList.add('active');
  });

  // Hedge funds sub-tabs (Holdings / Buys / Sells)
  document.addEventListener('click', e => {
    const btn = e.target.closest('.hf-tab-btn');
    if (!btn) return;
    const tabKey = btn.dataset.hftab;
    document.querySelectorAll('.hf-tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.hf-section').forEach(s => s.classList.remove('active'));
    btn.classList.add('active');
    const section = document.querySelector('.hf-section[data-hftab="' + tabKey + '"]');
    if (section) section.classList.add('active');
    // Re-apply filters so visible counts match the newly-shown table.
    if (typeof applyHedgeFilters === 'function') applyHedgeFilters();
  });

  // Picks filters
  const fMin = document.getElementById('fMinScore');
  const vMin = document.getElementById('vMin');
  const fBucket = document.getElementById('fBucket');
  const fSector = document.getElementById('fSector');
  const fMoat = document.getElementById('fMoat');
  const fStrength = document.getElementById('fStrength');
  const fValuation = document.getElementById('fValuation');
  const fSearch = document.getElementById('fSearch');

  function applyPickFilters() {
    const min = parseFloat(fMin.value) || 0;
    vMin.textContent = min;
    const bucket = fBucket.value;
    const sector = fSector.value;
    const moat = fMoat.value;
    const strength = fStrength.value;
    const val = fValuation.value;
    const q = fSearch.value.trim().toLowerCase();

    let strongCount = 0, angleCount = 0, partialCount = 0;
    document.querySelectorAll('.pick').forEach(card => {
      const score = parseFloat(card.dataset.score);
      const bk = card.dataset.bucket;
      const ok = (
        score >= min &&
        (!bucket || bucket === bk) &&
        (!sector || card.dataset.sector === sector) &&
        (!moat || parseFloat(card.dataset.moat) >= 70) &&
        (!strength || parseFloat(card.dataset.strength) >= 70) &&
        (!val || parseFloat(card.dataset.valuation) >= 70) &&
        (!q || card.dataset.search.indexOf(q) !== -1)
      );
      card.classList.toggle('hidden', !ok);
      if (ok) {
        if (bk === 'strong') strongCount++;
        else if (bk === 'angle') angleCount++;
        else if (bk === 'partial') partialCount++;
      }
    });
    document.getElementById('strongCount').textContent = strongCount;
    document.getElementById('angleCount').textContent = angleCount;
    const pc = document.getElementById('partialCount');
    if (pc) pc.textContent = partialCount;
  }
  [fMin, fBucket, fSector, fMoat, fStrength, fValuation].forEach(
    el => el.addEventListener('input', applyPickFilters));
  fSearch.addEventListener('input', applyPickFilters);

  // Briefing filters
  const bTopic = document.getElementById('bTopic');
  const bSource = document.getElementById('bSource');
  const bTier1 = document.getElementById('bTier1');
  const bSearch = document.getElementById('bSearch');

  function applyBriefingFilters() {
    const topic = bTopic.value;
    const source = bSource.value;
    const tier1 = bTier1.value;
    const q = bSearch.value.trim().toLowerCase();

    const topicCounts = {};
    document.querySelectorAll('.article').forEach(a => {
      const ok = (
        (!topic || a.dataset.topic === topic) &&
        (!source || a.dataset.source === source) &&
        (!tier1 || a.dataset.tier === '1') &&
        (!q || a.dataset.search.indexOf(q) !== -1)
      );
      a.classList.toggle('hidden', !ok);
      if (ok) topicCounts[a.dataset.topic] = (topicCounts[a.dataset.topic] || 0) + 1;
    });
    document.querySelectorAll('.brief-topic').forEach(t => {
      const c = topicCounts[t.dataset.topic] || 0;
      t.style.display = c ? '' : 'none';
      const meta = t.querySelector('.topic-meta');
      if (meta) meta.textContent = c + ' articles · past 7 days';
    });
  }
  [bTopic, bSource, bTier1].forEach(el => el.addEventListener('change', applyBriefingFilters));
  bSearch.addEventListener('input', applyBriefingFilters);

  // Hedge funds filters
  const hSector = document.getElementById('hSector');
  const hSearch = document.getElementById('hSearch');
  const hOverlap = document.getElementById('hOverlap');

  function applyHedgeFilters() {
    const sector = hSector.value;
    const overlap = hOverlap.value;
    const q = hSearch.value.trim().toLowerCase();
    // Filter rows in all sections; tab visibility is controlled by .active
    // class, so we don't touch section.style.display here.
    document.querySelectorAll('.hf-section').forEach(section => {
      section.querySelectorAll('tbody tr').forEach(row => {
        const ok = (
          (!sector || row.dataset.sector === sector) &&
          (!overlap || row.dataset.overlap === '1') &&
          (!q || row.dataset.search.indexOf(q) !== -1)
        );
        row.classList.toggle('hidden', !ok);
      });
    });
  }
  [hSector, hOverlap].forEach(el => el.addEventListener('change', applyHedgeFilters));
  hSearch.addEventListener('input', applyHedgeFilters);

  // Recommendations filters
  const rTier = document.getElementById('rTier');
  const rSector = document.getElementById('rSector');
  const rMin = document.getElementById('rMin');
  const rMinVal = document.getElementById('rMinVal');
  const rSearch = document.getElementById('rSearch');

  function applyRecFilters() {
    if (!rTier) return;  // no recs rendered
    const tier = rTier.value;
    const sector = rSector.value;
    const min = parseFloat(rMin.value) || 0;
    rMinVal.textContent = min;
    const q = rSearch.value.trim().toLowerCase();

    let visible = 0;
    document.querySelectorAll('.rec').forEach(card => {
      const composite = parseFloat(card.dataset.composite);
      const ok = (
        (!tier || card.dataset.tier === tier) &&
        (!sector || card.dataset.sector === sector) &&
        composite >= min &&
        (!q || card.dataset.search.indexOf(q) !== -1)
      );
      card.classList.toggle('hidden', !ok);
      if (ok) visible++;
    });
    document.getElementById('recCount').textContent = visible;
  }
  if (rTier) {
    [rTier, rSector, rMin].forEach(el => el.addEventListener('input', applyRecFilters));
    rSearch.addEventListener('input', applyRecFilters);
  }

  // Berkshire holdings filters
  const brkAct = document.getElementById('brkAct');
  const brkSearch = document.getElementById('brkSearch');
  const brkOverlap = document.getElementById('brkOverlap');
  function applyBrkFilters() {
    if (!brkAct) return;
    const act = brkAct.value;
    const overlap = brkOverlap.value;
    const q = brkSearch.value.trim().toLowerCase();
    let n = 0;
    document.querySelectorAll('#berkshire tbody tr').forEach(row => {
      const rowAct = row.dataset.act;
      const actOk = !act || (act === 'any' ? rowAct !== 'none' : rowAct === act);
      const ok = (
        actOk &&
        (!overlap || row.dataset.overlap === '1') &&
        (!q || row.dataset.search.indexOf(q) !== -1)
      );
      row.classList.toggle('hidden', !ok);
      if (ok) n++;
    });
    const c = document.getElementById('brkCount');
    if (c) c.textContent = n;
  }
  if (brkAct) {
    [brkAct, brkOverlap].forEach(el => el.addEventListener('change', applyBrkFilters));
    brkSearch.addEventListener('input', applyBrkFilters);
  }

  // ============== Market KPIs ==============
  const kpiDataEl = document.getElementById('kpiData');
  if (kpiDataEl) {
    const ALL_ROWS = JSON.parse(kpiDataEl.textContent);
    const PAGE_SIZE = 50;
    let filtered = ALL_ROWS.slice();
    let page = 0;
    let sortKey = 'mc';
    let sortDir = -1;  // -1 desc, 1 asc

    const tbody = document.getElementById('kpiBody');
    const kSector = document.getElementById('kSector');
    const kSearch = document.getElementById('kSearch');
    const kMcap = document.getElementById('kMcap');
    const kPe = document.getElementById('kPe');
    const kDy = document.getElementById('kDy');
    const kpiTotal = document.getElementById('kpiTotal');
    const kpiPageInfo = document.getElementById('kpiPageInfo');
    const kpiPrev = document.getElementById('kpiPrev');
    const kpiNext = document.getElementById('kpiNext');

    function fmtMcap(v) {
      if (v == null) return '—';
      if (v >= 1e12) return '$' + (v / 1e12).toFixed(2) + 'T';
      if (v >= 1e9)  return '$' + (v / 1e9).toFixed(2) + 'B';
      if (v >= 1e6)  return '$' + (v / 1e6).toFixed(0) + 'M';
      return '$' + Math.round(v);
    }
    function fmtNum(v, suffix) {
      if (v == null) return '<span class="na">—</span>';
      return v.toFixed(suffix === '%' ? 1 : 2) + (suffix || '');
    }
    function fmtPrice(v) {
      return v == null ? '<span class="na">—</span>' : '$' + v.toFixed(2);
    }

    function render() {
      const start = page * PAGE_SIZE;
      const slice = filtered.slice(start, start + PAGE_SIZE);
      const html = slice.map(r => {
        const sc = r.sc ? r.sc.replace(/</g, '&lt;') : '';
        const nm = r.n ? r.n.replace(/</g, '&lt;') : '';
        return '<tr>' +
          '<td class="ticker">' + r.t + '</td>' +
          '<td class="name" title="' + nm + '">' + nm + '</td>' +
          '<td>' + sc + '</td>' +
          '<td class="num">' + fmtPrice(r.px) + '</td>' +
          '<td class="num">' + (r.mc == null ? '<span class="na">—</span>' : fmtMcap(r.mc)) + '</td>' +
          '<td class="num">' + fmtNum(r.pe) + '</td>' +
          '<td class="num">' + fmtNum(r.fpe) + '</td>' +
          '<td class="num">' + fmtNum(r.pb) + '</td>' +
          '<td class="num">' + fmtNum(r.dy, '%') + '</td>' +
          '<td class="num">' + fmtNum(r.fcy, '%') + '</td>' +
          '<td class="num">' + fmtNum(r.roe, '%') + '</td>' +
          '<td class="num">' + fmtNum(r.de) + '</td>' +
          '<td class="num">' + fmtNum(r.beta) + '</td>' +
          '<td class="num">' + fmtNum(r.score) + '</td>' +
        '</tr>';
      }).join('');
      if (!html) {
        const q = (kSearch.value || '').trim();
        if (q) {
          // Search miss → graceful fallback to Yahoo Finance for whatever the
          // user typed. Works for any global ticker (NVDA, ASML.AS, 7203.T…).
          const safe = q.replace(/[^A-Za-z0-9.\-]/g, '');
          const upper = safe.toUpperCase();
          const url = 'https://finance.yahoo.com/quote/' + encodeURIComponent(upper);
          tbody.innerHTML = '<tr class="kpi-fallback"><td colspan="14">' +
            '<strong>"' + q.replace(/</g, '&lt;') + '" isn\'t in this run\'s universe.</strong> ' +
            'Open <a class="yf-ticker" href="' + url + '" target="_blank">' + upper + ' on Yahoo Finance ↗</a> ' +
            'for live quote and fundamentals. (For non-US names use an exchange suffix: ' +
            '<code>.L</code>, <code>.T</code>, <code>.HK</code>, <code>.PA</code>, <code>.AS</code>, ' +
            '<code>.TO</code>, <code>.AX</code>, <code>.NS</code> …)' +
            '</td></tr>';
        } else {
          tbody.innerHTML = '<tr><td colspan="14" class="empty">No matches.</td></tr>';
        }
      } else {
        tbody.innerHTML = html;
      }
      const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
      kpiTotal.textContent = filtered.length;
      kpiPageInfo.textContent = 'page ' + (page + 1) + ' of ' + totalPages;
      kpiPrev.disabled = page === 0;
      kpiNext.disabled = page >= totalPages - 1;
    }

    function applyFilters() {
      const sector = kSector.value;
      const q = kSearch.value.trim().toLowerCase();
      const minMcap = parseFloat(kMcap.value) || 0;
      const maxPe = kPe.value ? parseFloat(kPe.value) : null;
      const minDy = kDy.value ? parseFloat(kDy.value) : null;
      filtered = ALL_ROWS.filter(r => {
        if (sector && r.sc !== sector) return false;
        if (q && (r.t + ' ' + (r.n || '')).toLowerCase().indexOf(q) === -1) return false;
        if (minMcap && (r.mc == null || r.mc < minMcap)) return false;
        if (maxPe !== null && (r.pe == null || r.pe > maxPe)) return false;
        if (minDy !== null && (r.dy == null || r.dy < minDy)) return false;
        return true;
      });
      sortFiltered();
      page = 0;
      render();
    }

    function sortFiltered() {
      const dir = sortDir;
      filtered.sort((a, b) => {
        const av = a[sortKey], bv = b[sortKey];
        // nulls sink to end regardless of direction
        if (av == null && bv == null) return 0;
        if (av == null) return 1;
        if (bv == null) return -1;
        if (typeof av === 'string') return av.localeCompare(bv) * dir;
        return (av - bv) * dir;
      });
    }

    document.querySelectorAll('#kpiTable thead th').forEach(th => {
      th.addEventListener('click', () => {
        const key = th.dataset.sort;
        if (!key) return;
        if (key === sortKey) {
          sortDir = -sortDir;
        } else {
          sortKey = key;
          sortDir = th.dataset.num === '1' ? -1 : 1;  // numbers default desc, text asc
        }
        document.querySelectorAll('#kpiTable thead th').forEach(x => {
          const ind = x.querySelector('.sort-ind');
          if (ind) ind.remove();
        });
        const span = document.createElement('span');
        span.className = 'sort-ind';
        span.textContent = sortDir === -1 ? '▼' : '▲';
        th.appendChild(span);
        sortFiltered();
        page = 0;
        render();
      });
    });

    [kSector, kMcap, kPe, kDy].forEach(el => el.addEventListener('change', applyFilters));
    kSearch.addEventListener('input', applyFilters);
    kpiPrev.addEventListener('click', () => { if (page > 0) { page--; render(); } });
    kpiNext.addEventListener('click', () => {
      const totalPages = Math.ceil(filtered.length / PAGE_SIZE);
      if (page < totalPages - 1) { page++; render(); }
    });

    sortFiltered();
    render();
  }

  // ============== Cockpit ==============
  const ckDataEl = document.getElementById('ckData');
  if (ckDataEl) {
    const CK_ROWS = JSON.parse(ckDataEl.textContent);
    const CK_BY = {};
    CK_ROWS.forEach(r => { CK_BY[r.t] = r; });

    const picker = document.getElementById('ckPicker');
    const nameEl = document.getElementById('ckName');
    const sectorEl = document.getElementById('ckSector');
    const scoreEl = document.getElementById('ckScore');
    const tickerBadge = document.getElementById('ckTickerBadge');
    const chartMount = document.getElementById('ckChartMount');
    const yahooLink = document.getElementById('ckYahooLink');
    const cockpitSection = document.getElementById('cockpit');

    function esc(s) {
      return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }
    function cockpitVisible() {
      return cockpitSection && cockpitSection.classList.contains('active');
    }

    function fmtMcapCK(v) {
      if (v == null) return '—';
      if (v >= 1e12) return '$' + (v / 1e12).toFixed(2) + 'T';
      if (v >= 1e9)  return '$' + (v / 1e9).toFixed(2) + 'B';
      if (v >= 1e6)  return '$' + (v / 1e6).toFixed(0) + 'M';
      return '$' + Math.round(v);
    }
    function v(val, suffix, digits) {
      if (val == null) return '<span class="val na">—</span>';
      const d = digits == null ? 2 : digits;
      return '<span class="val">' + Number(val).toFixed(d) + (suffix || '') + '</span>';
    }
    function vTxt(val) {
      if (val == null || val === '') return '<span class="val na">—</span>';
      return '<span class="val">' + String(val).replace(/</g, '&lt;') + '</span>';
    }
    function row(lbl, valHtml) {
      return '<div class="ck-row"><span class="lbl">' + lbl + '</span>' + valHtml + '</div>';
    }
    function dimBar(d) {
      const sc = d.s == null ? 0 : d.s;
      const cls = sc >= 70 ? '' : (sc >= 45 ? 'mid' : 'low');
      return '<div class="dim-bar">' +
        '<span class="lbl" style="font-size:11px;color:#555;">' + d.n + '</span>' +
        '<span class="val" style="font-family:ui-monospace,Menlo,monospace;font-weight:700;font-size:12px;">' + sc + '</span>' +
        '<div class="bar-track"><div class="bar-fill ' + cls + '" style="width:' + Math.min(100, Math.max(0, sc)) + '%;"></div></div>' +
      '</div>';
    }
    function tradingViewWidget(ticker) {
      // Rebuild the widget container from scratch — TradingView's embed script
      // mutates the node, so swapping innerHTML alone leaves stale state.
      chartMount.innerHTML = '';
      const container = document.createElement('div');
      container.className = 'tradingview-widget-container';
      container.style.height = '100%';
      container.style.width = '100%';
      const inner = document.createElement('div');
      inner.className = 'tradingview-widget-container__widget';
      inner.style.height = '100%';
      inner.style.width = '100%';
      container.appendChild(inner);
      const script = document.createElement('script');
      script.type = 'text/javascript';
      script.src = 'https://s3.tradingview.com/external-embedding/embed-widget-mini-symbol-overview.js';
      script.async = true;
      script.innerHTML = JSON.stringify({
        symbol: ticker,
        width: '100%',
        height: '100%',
        locale: 'en',
        dateRange: '12M',
        colorTheme: 'light',
        trendLineColor: 'rgba(26, 26, 26, 1)',
        underLineColor: 'rgba(26, 26, 26, 0.15)',
        underLineBottomColor: 'rgba(26, 26, 26, 0)',
        isTransparent: true,
        autosize: true,
        largeChartUrl: ''
      });
      container.appendChild(script);
      chartMount.appendChild(container);
    }

    function render(ticker) {
      const c = CK_BY[ticker];
      if (!c) return;

      // Header
      nameEl.textContent = c.n || '';
      sectorEl.textContent = c.sc || 'Unknown sector';
      const sc = c.score == null ? 0 : c.score;
      scoreEl.className = 'ck-score' + (sc >= 75 ? '' : (sc >= 60 ? ' angle' : ' weak'));
      scoreEl.textContent = (c.score == null ? '—' : c.score.toFixed(0)) + ' / 100';
      tickerBadge.textContent = c.t;
      yahooLink.href = 'https://finance.yahoo.com/quote/' + encodeURIComponent(c.t);

      // Chart — only build when the Cockpit tab is actually visible. The
      // TradingView mini-chart needs a sized container to mount; building it
      // while the section is hidden yields a 0×0 widget that never recovers.
      // The tab-click handler builds it on first reveal.
      if (cockpitVisible()) tradingViewWidget(c.t);

      // 12 — Price & Size
      document.getElementById('ckPrice').innerHTML =
        row('Price', c.px == null ? '<span class="val na">—</span>' : '<span class="val">$' + c.px.toFixed(2) + '</span>') +
        row('Market Cap', '<span class="val">' + fmtMcapCK(c.mc) + '</span>') +
        row('Analyst tgt', c.tgtMean == null
            ? '<span class="val na">—</span>'
            : '<span class="val">$' + c.tgtMean.toFixed(2)
              + (c.recKey ? '<span class="sub">' + c.recKey + '</span>' : '')
              + '</span>');

      // 1:30 — Valuation
      document.getElementById('ckValuation').innerHTML =
        row('P/E (TTM)', v(c.pe)) +
        row('Fwd P/E', v(c.fpe)) +
        row('P/B', v(c.pb)) +
        row('PEG', v(c.peg)) +
        row('P/S', v(c.ps)) +
        row('EV / EBITDA', v(c.evEbitda));

      // 3 — Cash Returns
      document.getElementById('ckCash').innerHTML =
        row('FCF yield', v(c.fcy, '%', 2)) +
        row('Dividend yield', v(c.dy, '%', 2)) +
        row('Shareholder yld', v(c.shy, '%', 2));

      // 4:30 — Profitability
      document.getElementById('ckProfit').innerHTML =
        row('ROE (10y avg)', v(c.roe, '%', 1)) +
        row('ROIC (10y avg)', v(c.roic, '%', 1)) +
        row('Op margin', v(c.om, '%', 1)) +
        row('Net margin', v(c.nm, '%', 1)) +
        row('Gross margin', v(c.gm, '%', 1));

      // 6 — Verdict
      const tierHtml = c.tier
        ? '<span class="ck-tier-pill tier-badge ' + c.tier + '">' + c.tier + '</span>'
        : '';
      document.getElementById('ckVerdict').innerHTML =
        row('Buffett score', '<span class="val">' + (c.score == null ? '—' : c.score.toFixed(0)) + '</span>') +
        row('Composite', c.composite == null
            ? '<span class="val na">—</span>'
            : '<span class="val">' + c.composite.toFixed(0) + tierHtml + '</span>') +
        row('Margin of safety', v(c.mos, '%', 0)) +
        row('Intrinsic / share', c.iv == null
            ? '<span class="val na">—</span>'
            : '<span class="val">$' + c.iv.toFixed(2) + '</span>');

      // 7:30 — Balance Sheet
      document.getElementById('ckBalance').innerHTML =
        row('Debt / Equity', v(c.de)) +
        row('Current ratio', v(c.cr)) +
        row('Interest cov', v(c.ic, 'x', 1));

      // 9 — Dimensions
      const dimsHtml = (c.dims || []).map(dimBar).join('');
      document.getElementById('ckDims').innerHTML = dimsHtml ||
        '<div style="color:var(--muted);font-size:12px;">No dimensions</div>';

      // 10:30 — Risk
      const rangeTxt = (c.w52l != null && c.w52h != null)
        ? '$' + c.w52l.toFixed(2) + ' – $' + c.w52h.toFixed(2)
        : null;
      document.getElementById('ckRisk').innerHTML =
        row('Beta', v(c.beta)) +
        row('52w range', rangeTxt
            ? '<span class="val" style="font-size:11.5px;">' + rangeTxt + '</span>'
            : '<span class="val na">—</span>') +
        row('Smart-money holders', '<span class="val">' + (c.holdings || 0) + '</span>') +
        row('Buys / Sells last qtr',
            '<span class="val">' + (c.buys || 0) + ' / ' + (c.sells || 0) + '</span>');

      // Lower: thesis + about + news. Escape the thesis before substituting
      // <br> so a stray "<" in company/sector text can't inject markup.
      const thesisHtml = c.thesis
        ? esc(c.thesis).replace(/\n\n/g, '<br><br>')
        : '<span style="color:var(--muted);font-style:italic;">No thesis available.</span>';
      document.getElementById('ckThesis').innerHTML = thesisHtml;
      document.getElementById('ckAbout').textContent = c.about || '';

      const newsEl = document.getElementById('ckNews');
      if (!c.news || !c.news.length) {
        newsEl.innerHTML = '<div class="no-content">No recent news.</div>';
      } else {
        newsEl.innerHTML = c.news.map(n =>
          '<div class="news-item">' +
            '<a href="' + n.u + '" target="_blank">' + n.t.replace(/</g, '&lt;') + '</a>' +
            '<div class="meta">' + (n.p || '') + (n.d ? ' · ' + n.d : '') + '</div>' +
          '</div>'
        ).join('');
      }
    }

    picker.addEventListener('change', () => render(picker.value));

    // Build the chart when the Cockpit tab first becomes visible. render()
    // intentionally skips the chart while the section is hidden (a TradingView
    // mini-chart mounted in a 0×0 container never recovers), so this is the
    // only place the initial chart gets built. Guard against rebuilding if the
    // user re-clicks the already-active tab.
    document.querySelectorAll('.tab-btn[data-target="cockpit"]').forEach(b => {
      b.addEventListener('click', () => {
        if (!chartMount.querySelector('iframe, .tradingview-widget-container')) {
          tradingViewWidget(picker.value);
        }
      });
    });

    // Populate KPIs/thesis/news for the default ticker up front; the chart
    // builds lazily on first tab reveal (see above).
    if (CK_ROWS.length) render(CK_ROWS[0].t);
  }
})();
</script>
</body></html>
"""


_PICK_CARD = r"""
{% macro pick_card(p, bucket) %}
<div class="pick" data-bucket="{{ bucket }}"
     data-score="{{ p.score.total }}"
     data-sector="{{ p.score.sector }}"
     data-moat="{{ dim_score(p, 'Moat & Profitability') }}"
     data-strength="{{ dim_score(p, 'Financial Strength') }}"
     data-consistency="{{ dim_score(p, 'Consistency') }}"
     data-valuation="{{ dim_score(p, 'Valuation / Margin of Safety') }}"
     data-capalloc="{{ dim_score(p, 'Capital Allocation') }}"
     data-search="{{ (p.score.ticker + ' ' + p.score.name)|lower }}">
  <div class="pick-head">
    <span class="ticker">{{ p.score.ticker }}</span>
    <span class="name">{{ p.score.name }}</span>
    <span class="score-pill {{ bucket }}">{{ '%.1f'|format(p.score.total) }}</span>
  </div>
  <div class="facts">
    <span>{{ p.score.sector or 'Unknown sector' }}</span>
    {% if p.score.valuation.price %}<span>${{ '%.2f'|format(p.score.valuation.price) }}</span>{% endif %}
    {% if p.score.valuation.fcf_yield_pct is not none %}<span>FCF yield {{ '%.1f'|format(p.score.valuation.fcf_yield_pct) }}%</span>{% endif %}
    {% if p.score.valuation.margin_of_safety_pct is not none %}<span>MoS {{ '%.0f'|format(p.score.valuation.margin_of_safety_pct) }}%</span>{% endif %}
  </div>

  <div class="dim-grid">
    {% for d in p.score.dimensions %}
    <div class="dim">
      <div class="dim-head"><span>{{ d.name }}</span><span class="ds">{{ '%.0f'|format(d.score) }}</span></div>
      <div class="cells">
        {% for c in d.cells %}
        <div class="cell {{ c.status }}">
          <span class="lbl">{{ c.label }}</span>
          <span class="val">{{ c.display() }}</span>
          <span class="tgt">≥{{ c.target }}{{ c.unit }}</span>
        </div>
        {% endfor %}
      </div>
    </div>
    {% endfor %}
  </div>

  {% set full_desc = full_description(p) %}
  {% set news = stock_news.get(p.score.ticker, []) %}

  <div class="card-tabs">
    <div class="card-tab-bar">
      <button class="card-tab-btn active" data-tab="about">About</button>
      <button class="card-tab-btn" data-tab="analysis">Buffett Analysis</button>
      <button class="card-tab-btn" data-tab="news">News{% if news %} ({{ news|length }}){% endif %}</button>
    </div>

    <div class="card-tab-content active" data-tab="about">
      {% if full_desc %}
      <div class="descr-body">{{ full_desc }}</div>
      {% else %}
      <div class="no-content">No business description available.</div>
      {% endif %}
    </div>

    <div class="card-tab-content" data-tab="analysis">
      <div class="thesis">
{{ p.thesis.as_markdown() | replace('**', '') | replace('_', '') | replace('\n\n', '<br><br>') | safe }}
      </div>
    </div>

    <div class="card-tab-content" data-tab="news">
      {% if news %}
        {% for n in news %}
        <div class="news-item">
          <a href="{{ n.url }}" target="_blank">{{ n.title }}</a>
          <div class="meta">{{ n.publisher }}{% if n.published_at %} · {{ n.published_at.strftime('%b %d') }}{% endif %}</div>
          {% if n.summary %}<div class="sum">{{ n.summary }}</div>{% endif %}
        </div>
        {% endfor %}
      {% else %}
      <div class="no-content">No recent news.</div>
      {% endif %}
    </div>
  </div>
</div>
{% endmacro %}
"""


def _dim_score(p: Pick, name: str) -> float:
    for d in p.score.dimensions:
        if d.name == name:
            return round(d.score, 1)
    return 0.0


def render_dashboard(
    strong: list[Pick],
    angles: list[Pick],
    briefing: Briefing,
    stock_news: dict[str, list[NewsItem]],
    hedge_views: dict[ViewKind, HedgeFundView] | None = None,
    recommendations: list[Recommendation] | None = None,
    brk_portfolio: ManagerPortfolio | None = None,
    kpi_rows: list[dict[str, Any]] | None = None,
    cache_ttl_hours: int | None = None,
    partial: list[Pick] | None = None,
    cockpit_data: list[dict[str, Any]] | None = None,
) -> str:
    from markupsafe import Markup

    env = Environment(autoescape=True)
    env.globals["dim_score"] = _dim_score
    env.globals["stock_news"] = stock_news
    env.globals["full_description"] = _full_description
    macros = env.from_string(_PICK_CARD).module
    template = env.from_string(_TEMPLATE)
    template.globals["pick_card"] = macros.pick_card

    partial = partial or []
    all_picks = strong + angles + partial
    sectors = sorted({p.score.sector for p in all_picks if p.score.sector})
    briefing_sources = sorted({
        a.source for items in briefing.topics.values() for a in items
    })

    hedge_views = hedge_views or {}
    # Preserve a stable order: holdings → buys → sells
    ordered_views = {k: hedge_views[k] for k in ("holdings", "buys", "sells")
                     if k in hedge_views}
    hedge_sectors = sorted({r.sector for v in ordered_views.values() for r in v.rows
                            if r.sector})
    surfaced_tickers = {p.score.ticker for p in all_picks}
    any_hedge_rows = any(v.rows for v in ordered_views.values())

    recommendations = recommendations or []
    rec_sectors = sorted({r.pick.score.sector for r in recommendations
                          if r.pick.score.sector})

    kpi_rows = kpi_rows or []
    kpi_sectors = sorted({r["sc"] for r in kpi_rows if r.get("sc")})
    # Embed JSON in a <script type="application/json"> block.
    # Escape `</` so a stray "</script>" inside data can't terminate the block.
    kpi_json_str = json.dumps(kpi_rows, separators=(",", ":")).replace("</", "<\\/")

    cockpit_data = cockpit_data or []
    cockpit_json_str = json.dumps(cockpit_data, separators=(",", ":")).replace("</", "<\\/")

    now = datetime.now()
    return template.render(
        strong=strong,
        angles=angles,
        partial=partial,
        sectors=sectors,
        briefing=briefing,
        briefing_sources=briefing_sources,
        hedge_views=ordered_views,
        hedge_sectors=hedge_sectors,
        surfaced_tickers=surfaced_tickers,
        any_hedge_rows=any_hedge_rows,
        recommendations=recommendations,
        rec_sectors=rec_sectors,
        brk=brk_portfolio,
        brk_url="https://www.dataroma.com/m/holdings.php?m=BRK",
        kpi_rows=kpi_rows,
        kpi_sectors=kpi_sectors,
        kpi_json=Markup(kpi_json_str),
        cockpit_data=cockpit_data,
        cockpit_json=Markup(cockpit_json_str),
        cache_ttl_hours=cache_ttl_hours or 168,
        date=now.strftime("%A, %B %d %Y"),
        generated_at=now.strftime("%Y-%m-%d %H:%M"),
    )
