"""Idempotent upsert into a Notion database keyed by Ticker.

DB schema expected (create once in Notion; bot won't migrate schemas):

  Ticker            (title)
  Name              (rich_text)
  Sector            (select)
  Total Score       (number)
  Moat              (number)
  Strength          (number)
  Consistency       (number)
  Valuation         (number)
  CapAlloc          (number)
  Price             (number)
  FCF Yield %       (number)
  MoS %             (number)
  Last Updated      (date)
  Thesis            (rich_text)
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Iterable

from notion_client import Client

from ..pipeline import Pick

log = logging.getLogger(__name__)

_MAX_RICH_TEXT = 1900  # Notion limit per rich_text element is 2000 chars


def _dim_score(p: Pick, name: str) -> float:
    for d in p.score.dimensions:
        if d.name == name:
            return round(d.score, 1)
    return 0.0


def _props(p: Pick) -> dict:
    s = p.score
    v = s.valuation
    thesis_md = p.thesis.as_markdown()
    if len(thesis_md) > _MAX_RICH_TEXT:
        thesis_md = thesis_md[: _MAX_RICH_TEXT - 1] + "…"

    props: dict = {
        "Ticker": {"title": [{"text": {"content": s.ticker}}]},
        "Name": {"rich_text": [{"text": {"content": s.name or ""}}]},
        "Total Score": {"number": float(s.total)},
        "Moat": {"number": _dim_score(p, "Moat & Profitability")},
        "Strength": {"number": _dim_score(p, "Financial Strength")},
        "Consistency": {"number": _dim_score(p, "Consistency")},
        "Valuation": {"number": _dim_score(p, "Valuation / Margin of Safety")},
        "CapAlloc": {"number": _dim_score(p, "Capital Allocation")},
        "Last Updated": {"date": {"start": datetime.now(timezone.utc).date().isoformat()}},
        "Thesis": {"rich_text": [{"text": {"content": thesis_md}}]},
    }
    if s.sector:
        props["Sector"] = {"select": {"name": s.sector[:100]}}
    if v.price is not None:
        props["Price"] = {"number": round(v.price, 2)}
    if v.fcf_yield_pct is not None:
        props["FCF Yield %"] = {"number": round(v.fcf_yield_pct, 2)}
    if v.margin_of_safety_pct is not None:
        props["MoS %"] = {"number": round(v.margin_of_safety_pct, 1)}
    return props


def sync_picks(picks: Iterable[Pick], notion_cfg: dict) -> None:
    api_key = os.environ.get("NOTION_API_KEY")
    if not api_key:
        raise RuntimeError("NOTION_API_KEY env var is not set — cannot sync to Notion.")
    db_id = notion_cfg.get("database_id")
    if not db_id:
        raise RuntimeError(
            "notion.database_id missing from settings — set NOTION_DATABASE_ID env var."
        )
    client = Client(auth=api_key)

    for p in picks:
        if p.score.error:
            continue
        ticker = p.score.ticker
        try:
            existing = client.databases.query(
                database_id=db_id,
                filter={"property": "Ticker", "title": {"equals": ticker}},
                page_size=1,
            ).get("results", [])
            props = _props(p)
            if existing:
                client.pages.update(page_id=existing[0]["id"], properties=props)
            else:
                client.pages.create(parent={"database_id": db_id}, properties=props)
        except Exception as e:
            log.warning("Notion upsert failed for %s: %s", ticker, e)
