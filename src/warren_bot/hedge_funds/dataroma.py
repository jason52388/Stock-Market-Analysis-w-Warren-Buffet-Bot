"""Scrape dataroma.com's superinvestor grid for hedge-fund holdings + activity.

Dataroma aggregates 13F filings from a curated list of well-known
"superinvestors" — Buffett, Ackman, Klarman, Greenblatt, Burry, etc. The grid
page exposes the same set of S&P 500 names sorted by different metrics:

  /m/grid.php           ownership count (largest holdings)
  /m/grid.php?s=q       last-quarter buys/adds (biggest accumulation)
  /m/grid.php?s=sq      last-quarter sells/reductions (biggest distribution)

A separate per-manager page exposes the full holdings of one superinvestor:

  /m/holdings.php?m=BRK   Berkshire Hathaway full portfolio (29-ish positions)

Each cell's tooltip contains: name, sector, metric label + value, hold price.
13F data updates ~45 days after quarter end, so we cache aggressively.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Literal

import requests
from bs4 import BeautifulSoup

from ..data.cache import Cache

log = logging.getLogger(__name__)

ATTRIBUTION_URL = "https://www.dataroma.com/m/grid.php"
BRK_HOLDINGS_URL = "https://www.dataroma.com/m/holdings.php?m=BRK"
USER_AGENT = "warren-bot/0.1 (+research; contact via repo)"

ViewKind = Literal["holdings", "buys", "sells"]

_URLS: dict[ViewKind, str] = {
    "holdings": "https://www.dataroma.com/m/grid.php",
    "buys":     "https://www.dataroma.com/m/grid.php?s=q",
    "sells":    "https://www.dataroma.com/m/grid.php?s=sq",
}


@dataclass
class HedgeFundRow:
    ticker: str
    name: str
    sector: str
    metric_label: str       # e.g. "Superinvestor Ownership" or "No. of Buys/Adds"
    metric_value: int       # owner count, buy count, sell count
    hold_price: float | None
    rank: int                # 1-based position in the dataroma grid


@dataclass
class HedgeFundView:
    kind: ViewKind
    rows: list[HedgeFundRow]
    title: str
    subtitle: str


def _parse_tooltip(text: str) -> tuple[str, str, str, int, float | None]:
    """Extract (name, sector, metric_label, metric_value, hold_price).

    Tooltip format examples:
      'Alphabet Inc. (Information Technology) Superinvestor Ownership : 39 Hold Price: $287.56'
      'Microsoft Corp. (Information Technology) No. of Buys/Adds: 19 Hold Price: $370.22'
      'Alphabet Inc. (Information Technology) No. of Sells/Reductions: 24'
    """
    name = ""
    sector = ""
    metric_label = ""
    metric_value = 0
    hold_price: float | None = None

    # Sector is parenthesized; name is everything before it.
    m_sector = re.search(r"^(.*?)\s*\(([^)]+)\)", text)
    if m_sector:
        name = m_sector.group(1).strip()
        sector = m_sector.group(2).strip()
        rest = text[m_sector.end():]
    else:
        rest = text

    # Metric: "<label> : <int>"
    m_metric = re.search(r"([A-Za-z./ ]+?)\s*[:|]\s*(-?\d[\d,]*)", rest)
    if m_metric:
        metric_label = m_metric.group(1).strip(" |:")
        try:
            metric_value = int(m_metric.group(2).replace(",", ""))
        except ValueError:
            metric_value = 0

    m_price = re.search(r"Hold Price:?\s*\$?([\d,]+(?:\.\d+)?)", text)
    if m_price:
        try:
            hold_price = float(m_price.group(1).replace(",", ""))
        except ValueError:
            pass

    return name, sector, metric_label, metric_value, hold_price


def _scrape_view(kind: ViewKind, max_rows: int) -> HedgeFundView:
    url = _URLS[kind]
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    rows: list[HedgeFundRow] = []
    for i, cell in enumerate(soup.select("table#grid td"), 1):
        if i > max_rows:
            break
        a = cell.find("a")
        tip = cell.find("div")
        if not a or not tip:
            continue
        ticker = a.get_text(strip=True)
        # Tooltip is the inner div; use whitespace-joined text.
        tooltip = tip.get_text(" ", strip=True)
        name, sector, metric_label, metric_value, hold_price = _parse_tooltip(tooltip)
        rows.append(HedgeFundRow(
            ticker=ticker.replace(".", "-"),  # match yfinance convention
            name=name or ticker,
            sector=sector,
            metric_label=metric_label,
            metric_value=metric_value,
            hold_price=hold_price,
            rank=i,
        ))

    titles = {
        "holdings": ("Largest holdings",
                     "S&P 500 stocks ranked by how many tracked superinvestors hold them."),
        "buys": ("Biggest accumulation",
                 "Stocks where the most tracked superinvestors added or initiated positions last quarter."),
        "sells": ("Biggest distribution",
                  "Stocks where the most tracked superinvestors trimmed or exited positions last quarter."),
    }
    title, subtitle = titles[kind]
    return HedgeFundView(kind=kind, rows=rows, title=title, subtitle=subtitle)


@dataclass
class ManagerPosition:
    ticker: str
    name: str
    portfolio_pct: float                  # share of manager's portfolio
    activity: str                          # "Buy", "Sell", "Add 43.24%", "Reduce 5.22%", ""
    activity_kind: Literal["buy", "add", "sell", "reduce", "none"]
    shares: int | None
    reported_price: float | None
    value_usd: float | None                # dollar value of the position
    current_price: float | None
    price_change_pct: float | None         # vs reported price
    rank: int


@dataclass
class ManagerPortfolio:
    manager_code: str                      # "BRK"
    manager_name: str                      # "Warren Buffett - Berkshire Hathaway"
    period: str                            # "Q1 2026"
    portfolio_date: str                    # "31 Mar 2026"
    portfolio_value_usd: float | None      # total value
    positions: list[ManagerPosition] = field(default_factory=list)

    @property
    def active_positions(self) -> list[ManagerPosition]:
        """Subset with non-empty recent activity (new buys, adds, sells, reductions)."""
        return [p for p in self.positions if p.activity_kind != "none"]


def _num(text: str) -> float | None:
    """Parse '$57,843,261,000' / '227,917,808' / '21.99' → float, or None."""
    if not text:
        return None
    cleaned = text.replace("$", "").replace(",", "").replace("%", "").strip()
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _classify_activity(text: str) -> tuple[str, Literal["buy", "add", "sell", "reduce", "none"]]:
    """Extract activity label + normalized kind from the activity cell text."""
    t = (text or "").strip()
    if not t:
        return "", "none"
    low = t.lower()
    if low.startswith("buy"):
        return t, "buy"
    if low.startswith("add"):
        return t, "add"
    if low.startswith("sell"):
        return t, "sell"
    if low.startswith("reduce"):
        return t, "reduce"
    return t, "none"


def _scrape_manager_portfolio(manager_code: str) -> ManagerPortfolio:
    url = f"https://www.dataroma.com/m/holdings.php?m={manager_code}"
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    manager_name = (soup.select_one("#f_name") or {}).get_text(strip=True) \
        if soup.select_one("#f_name") else manager_code
    period = portfolio_date = ""
    portfolio_value = None
    p2 = soup.select_one("#p2")
    if p2:
        for span_label in p2.find_all("span"):
            prev = (span_label.previous_sibling or "")
            label = prev.strip().rstrip(":").lower() if isinstance(prev, str) else ""
            val = span_label.get_text(strip=True)
            if "period" in label:
                period = val
            elif "portfolio date" in label:
                portfolio_date = val
            elif "portfolio value" in label:
                portfolio_value = _num(val)

    positions: list[ManagerPosition] = []
    for i, tr in enumerate(soup.select("table#grid tbody tr"), 1):
        cells = tr.find_all("td")
        if len(cells) < 11:
            continue
        stock_a = cells[1].find("a")
        if not stock_a:
            continue
        # Ticker is the link text; the trailing <span> holds " - Company Name"
        ticker = (stock_a.contents[0] if stock_a.contents else "").strip()
        span = stock_a.find("span")
        company = ""
        if span:
            company = span.get_text(" ", strip=True).lstrip("- ").strip()

        activity_text, activity_kind = _classify_activity(cells[3].get_text(" ", strip=True))

        positions.append(ManagerPosition(
            ticker=ticker.replace(".", "-"),  # match yfinance convention
            name=company or ticker,
            portfolio_pct=_num(cells[2].get_text(strip=True)) or 0.0,
            activity=activity_text,
            activity_kind=activity_kind,
            shares=int(_num(cells[4].get_text(strip=True)) or 0) or None,
            reported_price=_num(cells[5].get_text(strip=True)),
            value_usd=_num(cells[6].get_text(strip=True)),
            current_price=_num(cells[8].get_text(strip=True)),
            price_change_pct=_num(cells[9].get_text(strip=True)),
            rank=i,
        ))

    return ManagerPortfolio(
        manager_code=manager_code,
        manager_name=manager_name,
        period=period,
        portfolio_date=portfolio_date,
        portfolio_value_usd=portfolio_value,
        positions=positions,
    )


def fetch_manager_portfolio(cache: Cache, manager_code: str = "BRK") -> ManagerPortfolio | None:
    """Return a manager's full portfolio from dataroma, cached. Returns None on failure."""
    cached = cache.get("dataroma_manager", manager_code)
    if cached is not None and cached.positions:
        return cached
    try:
        portfolio = _scrape_manager_portfolio(manager_code)
        cache.set("dataroma_manager", manager_code, portfolio)
        return portfolio
    except Exception as e:
        log.warning("dataroma manager fetch failed for %s: %s", manager_code, e)
        return None


def fetch_hedge_fund_views(cache: Cache, *, max_rows: int = 50) -> dict[ViewKind, HedgeFundView]:
    """Return the three dataroma views, served from cache when fresh."""
    out: dict[ViewKind, HedgeFundView] = {}
    for kind in ("holdings", "buys", "sells"):
        cached = cache.get("dataroma", kind)
        if cached is not None and len(cached.rows) >= max_rows:
            out[kind] = cached
            continue
        try:
            view = _scrape_view(kind, max_rows=max_rows)
            cache.set("dataroma", kind, view)
            out[kind] = view
        except Exception as e:
            log.warning("dataroma fetch failed for %s: %s", kind, e)
            out[kind] = HedgeFundView(kind=kind, rows=[], title=kind.title(), subtitle="")
    return out
