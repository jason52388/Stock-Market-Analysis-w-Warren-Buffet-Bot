"""Shared pytest fixtures."""
from __future__ import annotations

import pytest


@pytest.fixture()
def tmp_cache(tmp_path):
    """A Cache backed by a per-test temp SQLite file. ttl_seconds=3600 (1h)."""
    from warren_bot.data.cache import Cache

    cache = Cache(tmp_path / "test.sqlite", ttl_seconds=3600)
    yield cache
    cache.close()


@pytest.fixture()
def base_settings():
    """Minimal settings dict matching config/settings.yaml's shape."""
    return {
        "weights": {
            "moat": 0.30,
            "strength": 0.20,
            "consistency": 0.20,
            "valuation": 0.20,
            "capital_allocation": 0.10,
        },
        "score_thresholds": {
            "strong_match": 75,
            "interesting_angle": 60,
            "partial_match": 45,
        },
        "criteria": {
            "roe_pct": {"target": 15, "excellent": 25},
            "roic_pct": {"target": 12, "excellent": 20},
            "gross_margin_pct": {"target": 40, "excellent": 60},
            "net_margin_pct": {"target": 10, "excellent": 20},
            "debt_to_equity": {"target": 0.5, "excellent": 0.2, "lower_is_better": True},
            "interest_coverage": {"target": 5, "excellent": 15},
            "current_ratio": {"target": 1.5, "excellent": 2.5},
            "years_profitable": {"target": 8, "excellent": 10},
            "revenue_cagr_pct": {"target": 5, "excellent": 12},
            "eps_cagr_pct": {"target": 8, "excellent": 15},
            "fcf_positive_years": {"target": 8, "excellent": 10},
            "fcf_yield_pct": {"target": 5, "excellent": 8},
            "pe_vs_median_ratio": {"target": 1.0, "excellent": 0.7, "lower_is_better": True},
            "margin_of_safety_pct": {"target": 20, "excellent": 40},
            "shareholder_yield_pct": {"target": 3, "excellent": 7},
            "share_count_cagr_pct": {"target": 0, "excellent": -2, "lower_is_better": True},
        },
        "data": {
            "cache_path": ".cache/test.sqlite",
            "cache_ttl_hours": 1,
            "yf_max_retries": 1,
            "yf_batch_size": 25,
            "yf_batch_sleep_sec": 0,
            "min_market_cap_usd": 0,
        },
        "universe": {"files": []},
        "delivery": {
            "email": {"enabled": False, "from_addr": "", "to_addr": "",
                      "smtp_host": "smtp.gmail.com", "smtp_port": 587},
            "notion": {"enabled": False, "database_id": ""},
        },
    }
