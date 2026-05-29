"""Email summary — short digest, top picks teaser. Dashboard goes as attachment."""
from __future__ import annotations

from datetime import datetime
from typing import Iterable

from jinja2 import Environment

from ..pipeline import Pick

_TEMPLATE = """\
<!doctype html>
<html><head><meta charset="utf-8"><title>Buffett Bot</title>
<style>
 body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        color: #1a1a1a; max-width: 640px; margin: 0 auto; padding: 20px; }
 h1 { font-size: 20px; margin: 0 0 4px; }
 .sub { color: #666; font-size: 13px; margin-bottom: 18px; }
 .callout { background: #f5f3ec; border-left: 3px solid #1a1a1a; padding: 12px 14px;
            border-radius: 0 8px 8px 0; font-size: 13.5px; margin: 14px 0 18px; }
 .callout strong { font-weight: 700; }
 table.summary { width: 100%; border-collapse: collapse; margin: 8px 0; }
 table.summary th, table.summary td { padding: 7px 10px; text-align: left;
   border-bottom: 1px solid #ececec; font-size: 13px; }
 table.summary th { color: #666; font-weight: 600; font-size: 11.5px;
                    text-transform: uppercase; letter-spacing: .04em; }
 table.summary tr:hover { background: #fafaf7; }
 .ticker { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-weight: 700; }
 .score { font-weight: 700; text-align: right; }
 .pill { display: inline-block; background: #e7f4ec; color: #1f8f4a; border-radius: 999px;
         padding: 2px 8px; font-size: 11px; font-weight: 700; }
 .pill.angle { background: #fdf5d8; color: #c69400; }
 .pill.partial { background: #efeee8; color: #6a6a6a; }
 h2 { font-size: 14px; text-transform: uppercase; letter-spacing: .04em;
      color: #666; border-top: 1px solid #ddd; padding-top: 14px; margin-top: 24px; }
 .footer { color: #888; font-size: 11.5px; margin-top: 30px; }
</style></head><body>

<h1>Buffett Bot — {{ date }}</h1>
<div class="sub">{{ total_scored }} tickers screened ·
   {{ strong|length }} strong matches · {{ angles|length }} interesting angles ·
   {{ partial|length }} partial matches</div>

<div class="callout">
  📎 <strong>Full dashboard attached</strong> — open <strong>dashboard.html</strong>
  in any browser. Tabs: <em>Recommended</em> (blended Buffett ∩ hedge-fund
  consensus picks), <em>Buffett picks</em> (strong / angles / partial with
  per-card About/Analysis/News), <em>Berkshire</em> (BRK's full 13F portfolio),
  <em>Hedge Funds</em> (dataroma 13F aggregates + Berkshire activity),
  <em>Market KPIs</em> (price/P/E/etc. table for the whole universe),
  <em>Weekly Briefing</em> ({{ briefing_count }} curated articles).
</div>

{% if strong %}
<h2>Strong matches</h2>
<table class="summary">
  <thead><tr><th>Ticker</th><th>Company</th><th>Sector</th><th>Bucket</th><th style="text-align:right">Score</th></tr></thead>
  <tbody>
  {% for p in strong %}
  <tr>
    <td class="ticker">{{ p.score.ticker }}</td>
    <td>{{ p.score.name }}</td>
    <td>{{ p.score.sector }}</td>
    <td><span class="pill">strong</span></td>
    <td class="score">{{ '%.1f'|format(p.score.total) }}</td>
  </tr>
  {% endfor %}
  </tbody>
</table>
{% endif %}

{% if angles %}
<h2>Interesting angles</h2>
<table class="summary">
  <thead><tr><th>Ticker</th><th>Company</th><th>Sector</th><th>Bucket</th><th style="text-align:right">Score</th></tr></thead>
  <tbody>
  {% for p in angles[:20] %}
  <tr>
    <td class="ticker">{{ p.score.ticker }}</td>
    <td>{{ p.score.name }}</td>
    <td>{{ p.score.sector }}</td>
    <td><span class="pill angle">angle</span></td>
    <td class="score">{{ '%.1f'|format(p.score.total) }}</td>
  </tr>
  {% endfor %}
  </tbody>
</table>
{% if angles|length > 20 %}
<div class="sub">+ {{ angles|length - 20 }} more angles in the attached dashboard.</div>
{% endif %}
{% endif %}

{% if partial %}
<h2>Partial matches</h2>
<table class="summary">
  <thead><tr><th>Ticker</th><th>Company</th><th>Sector</th><th>Bucket</th><th style="text-align:right">Score</th></tr></thead>
  <tbody>
  {% for p in partial[:15] %}
  <tr>
    <td class="ticker">{{ p.score.ticker }}</td>
    <td>{{ p.score.name }}</td>
    <td>{{ p.score.sector }}</td>
    <td><span class="pill partial">partial</span></td>
    <td class="score">{{ '%.1f'|format(p.score.total) }}</td>
  </tr>
  {% endfor %}
  </tbody>
</table>
{% if partial|length > 15 %}
<div class="sub">+ {{ partial|length - 15 }} more partial matches in the attached dashboard.</div>
{% endif %}
{% endif %}

{% if not strong and not angles and not partial %}
<p>No tickers cleared even the partial threshold this run. The market may be expensive,
or weights/thresholds may need recalibration.</p>
{% endif %}

<div class="footer">Generated {{ generated_at }} · Quantitative proxies for Buffett/Munger
criteria · DYOR before transacting.</div>
</body></html>
"""


def render_summary(strong: Iterable[Pick], angles: Iterable[Pick],
                   partial: Iterable[Pick] = (),
                   total_scored: int = 0, briefing_count: int = 0) -> str:
    env = Environment(autoescape=True)
    tmpl = env.from_string(_TEMPLATE)
    now = datetime.now()
    return tmpl.render(
        strong=list(strong),
        angles=list(angles),
        partial=list(partial),
        total_scored=total_scored,
        briefing_count=briefing_count,
        date=now.strftime("%A, %B %d %Y"),
        generated_at=now.strftime("%Y-%m-%d %H:%M"),
    )


# Backwards-compatible alias so the CLI can keep importing render_digest.
render_digest = render_summary
