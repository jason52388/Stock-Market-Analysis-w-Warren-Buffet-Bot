"""Weekly briefing — pulls RSS from a curated list and groups by topic.

Free, no API keys. Articles older than 7 days are dropped. A small relevance
score (keyword hits * source-tier weight) is used to rank within each topic.
"""
from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser
import yaml

from ..config import repo_root

log = logging.getLogger(__name__)

# Tier weights — Reuters/Bloomberg/FT count more than HN front page.
_TIER_WEIGHT = {1: 3.0, 2: 2.0, 3: 1.5, 4: 1.0}


@dataclass
class Article:
    title: str
    summary: str
    source: str
    tier: int
    url: str
    published_at: datetime | None
    topic: str = ""
    score: float = 0.0

    @property
    def display_date(self) -> str:
        if not self.published_at:
            return ""
        return self.published_at.strftime("%b %d")


@dataclass
class Briefing:
    generated_at: datetime
    topics: dict[str, list[Article]] = field(default_factory=dict)
    total_articles: int = 0

    @property
    def topic_list(self) -> list[tuple[str, list[Article]]]:
        return list(self.topics.items())


def _strip_html(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s or "").strip()


def _parse_entry_date(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            try:
                return datetime(*t[:6], tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
    return None


def _fetch_feed(source: dict, max_age_days: int) -> list[Article]:
    name = source["name"]
    tier = int(source["tier"])
    url = source["url"]
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    try:
        # feedparser handles its own networking; set agent to dodge 403s.
        parsed = feedparser.parse(url, agent="warren-bot/0.1 (+rss reader)")
    except Exception as e:
        log.warning("feed fetch failed for %s: %s", name, e)
        return []
    articles: list[Article] = []
    for entry in parsed.entries[:30]:
        pub = _parse_entry_date(entry)
        if pub and pub < cutoff:
            continue
        title = (entry.get("title") or "").strip()
        if not title:
            continue
        summary = _strip_html(entry.get("summary") or entry.get("description") or "")[:400]
        articles.append(Article(
            title=title,
            summary=summary,
            source=name,
            tier=tier,
            url=entry.get("link", ""),
            published_at=pub,
        ))
    return articles


def _classify(article: Article, topics_cfg: dict) -> tuple[str, float]:
    """Return (best_topic, score). Score is keyword hits × tier weight."""
    haystack = (article.title + " " + article.summary).lower()
    best_topic = ""
    best_hits = 0
    for topic, cfg in topics_cfg.items():
        hits = 0
        for kw in cfg["keywords"]:
            # Use word-boundary match for short keywords to avoid 'ai' inside 'said'.
            kw_l = kw.lower()
            if len(kw_l) <= 4:
                if re.search(rf"\b{re.escape(kw_l)}\b", haystack):
                    hits += 1
            elif kw_l in haystack:
                hits += 1
        if hits > best_hits:
            best_hits = hits
            best_topic = topic
    weight = _TIER_WEIGHT.get(article.tier, 1.0)
    return best_topic, best_hits * weight


def _dedupe(articles: list[Article]) -> list[Article]:
    seen: set[str] = set()
    out: list[Article] = []
    for a in articles:
        key = re.sub(r"[^a-z0-9]", "", a.title.lower())[:80]
        if key in seen:
            continue
        seen.add(key)
        out.append(a)
    return out


def build_briefing(
    config_path: Path | None = None,
    *,
    max_age_days: int = 7,
    per_topic: int = 8,
) -> Briefing:
    cfg_path = config_path or (repo_root() / "config" / "briefing_sources.yaml")
    cfg = yaml.safe_load(cfg_path.read_text()) or {}
    sources = cfg.get("sources")
    topics_cfg = cfg.get("topics")
    if not isinstance(sources, list) or not isinstance(topics_cfg, dict):
        raise ValueError(
            f"{cfg_path} is malformed: expected top-level 'sources' (list) and "
            f"'topics' (mapping). Got sources={type(sources).__name__}, "
            f"topics={type(topics_cfg).__name__}."
        )

    all_articles: list[Article] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_fetch_feed, s, max_age_days): s for s in sources}
        for fut in as_completed(futures):
            try:
                all_articles.extend(fut.result())
            except Exception as e:
                log.warning("feed task failed: %s", e)

    all_articles = _dedupe(all_articles)

    grouped: dict[str, list[Article]] = {t: [] for t in topics_cfg}
    for a in all_articles:
        topic, score = _classify(a, topics_cfg)
        if not topic:
            continue
        a.topic = topic
        a.score = score
        grouped[topic].append(a)

    for topic, items in grouped.items():
        items.sort(key=lambda x: (x.score, x.published_at or datetime.min.replace(tzinfo=timezone.utc)),
                   reverse=True)
        grouped[topic] = items[:per_topic]

    return Briefing(
        generated_at=datetime.now(timezone.utc),
        topics={t: items for t, items in grouped.items() if items},
        total_articles=sum(len(v) for v in grouped.values()),
    )
