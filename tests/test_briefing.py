"""Tests for the briefing aggregator — classification, dedup, config validation."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from warren_bot.news.briefing import Article, _classify, _dedupe, build_briefing


_TOPICS_CFG = {
    "AI": {"keywords": ["ai", "openai", "anthropic", "llm"]},
    "Semis": {"keywords": ["chip", "tsmc", "gpu"]},
    "Macro": {"keywords": ["inflation", "fed"]},
}


def _article(title, summary="", tier=1, source="Test"):
    return Article(
        title=title, summary=summary, source=source, tier=tier,
        url="https://example.com", published_at=datetime.now(timezone.utc),
    )


class TestClassify:
    def test_ai_topic_match(self):
        a = _article("OpenAI raises $40B in latest round")
        topic, score = _classify(a, _TOPICS_CFG)
        assert topic == "AI"
        assert score > 0

    def test_word_boundary_for_short_keyword(self):
        """Short keywords like 'ai' must match as a whole word, not inside 'said'."""
        a = _article("She said the market is volatile")  # 'ai' is inside 'said'
        topic, _ = _classify(a, _TOPICS_CFG)
        assert topic == ""  # no match — must not classify as AI

    def test_multiple_hits_higher_score(self):
        """More keyword hits + higher tier = higher score."""
        weak = _article("AI is mentioned once", tier=4)
        strong = _article("AI and OpenAI and Anthropic agree", tier=1)
        _, weak_score = _classify(weak, _TOPICS_CFG)
        _, strong_score = _classify(strong, _TOPICS_CFG)
        assert strong_score > weak_score

    def test_no_match_returns_empty(self):
        a = _article("A general news article about sports")
        topic, score = _classify(a, _TOPICS_CFG)
        assert topic == ""
        assert score == 0


class TestDedupe:
    def test_identical_titles_dedupe(self):
        a1 = _article("Anthropic Raises 65 Billion")
        a2 = _article("Anthropic Raises 65 Billion")
        result = _dedupe([a1, a2])
        assert len(result) == 1

    def test_title_normalization(self):
        """Punctuation/case differences should still dedupe."""
        a1 = _article("Anthropic Raises $65 Billion!")
        a2 = _article("ANTHROPIC RAISES 65 BILLION")
        result = _dedupe([a1, a2])
        assert len(result) == 1

    def test_distinct_titles_kept(self):
        result = _dedupe([_article("Story A"), _article("Story B")])
        assert len(result) == 2


class TestBuildBriefingValidation:
    """Regression test for the new schema validation."""

    def test_missing_sources_raises(self, tmp_path):
        cfg = tmp_path / "bad.yaml"
        cfg.write_text("topics:\n  AI: {keywords: [ai]}\n")
        with pytest.raises(ValueError, match="malformed"):
            build_briefing(config_path=cfg)

    def test_missing_topics_raises(self, tmp_path):
        cfg = tmp_path / "bad.yaml"
        cfg.write_text("sources:\n  - {name: x, tier: 1, url: 'http://x'}\n")
        with pytest.raises(ValueError, match="malformed"):
            build_briefing(config_path=cfg)

    def test_empty_yaml_raises(self, tmp_path):
        cfg = tmp_path / "empty.yaml"
        cfg.write_text("")
        with pytest.raises(ValueError, match="malformed"):
            build_briefing(config_path=cfg)

    def test_sources_as_dict_raises(self, tmp_path):
        cfg = tmp_path / "bad.yaml"
        cfg.write_text("sources: {not: a, list: True}\ntopics: {AI: {keywords: [ai]}}\n")
        with pytest.raises(ValueError, match="malformed"):
            build_briefing(config_path=cfg)
