"""Pluggable data-source adapters.

Each adapter fetches from one provider (yfinance, SEC EDGAR, FMP, Finnhub) and
normalizes the result into the canonical shape defined in ``data.schema`` so the
merge layer can combine them without any source-specific logic leaking out.
"""
from .base import SourceAdapter, SourceResult

__all__ = ["SourceAdapter", "SourceResult"]
