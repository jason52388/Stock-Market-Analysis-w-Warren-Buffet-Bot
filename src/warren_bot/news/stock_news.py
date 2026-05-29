"""Per-ticker recent news via yfinance.

yfinance ships a news endpoint that returns Yahoo Finance items. The shape
changed in 0.2.50+: each item is `{id, content: {...}}` where content has
`title`, `summary`, `pubDate`, `provider.displayName`, `canonicalUrl.url`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import yfinance as yf
from tenacity import retry, stop_after_attempt, wait_exponential

log = logging.getLogger(__name__)


@dataclass
class NewsItem:
    title: str
    summary: str
    publisher: str
    url: str
    published_at: datetime | None

    @property
    def age_days(self) -> int | None:
        if not self.published_at:
            return None
        return (datetime.now(timezone.utc) - self.published_at).days


def _parse_date(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


@retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=1, max=8))
def _raw_news(ticker: str) -> list[dict]:
    return yf.Ticker(ticker).news or []


def fetch_stock_news(ticker: str, *, limit: int = 3, max_age_days: int = 14) -> list[NewsItem]:
    """Return up to `limit` recent news items for a ticker, freshest first."""
    try:
        raw = _raw_news(ticker)
    except Exception as e:
        log.debug("news fetch failed for %s: %s", ticker, e)
        return []

    items: list[NewsItem] = []
    for entry in raw:
        c = entry.get("content") or entry
        title = c.get("title") or ""
        if not title:
            continue
        summary = (c.get("summary") or c.get("description") or "").strip()
        publisher = ((c.get("provider") or {}).get("displayName")) or "Yahoo Finance"
        url = (
            (c.get("canonicalUrl") or {}).get("url")
            or (c.get("clickThroughUrl") or {}).get("url")
            or ""
        )
        published_at = _parse_date(c.get("pubDate") or c.get("displayTime"))
        item = NewsItem(
            title=title,
            summary=summary[:300],
            publisher=publisher,
            url=url,
            published_at=published_at,
        )
        if item.age_days is not None and item.age_days > max_age_days:
            continue
        items.append(item)

    items.sort(
        key=lambda n: n.published_at or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return items[:limit]
