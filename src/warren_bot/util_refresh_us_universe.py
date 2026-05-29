"""Refresh config/universe_us.txt with all US common stocks.

Pulls the canonical symbol directories from nasdaqtrader.com — these are the
authoritative free lists of every NASDAQ-listed and other-exchange-listed
security. We filter to common stocks only: drop ETFs, ADRs (covered separately),
preferred shares, warrants, units, rights, test issues, and de-listed names.

Run quarterly: `python -m warren_bot.util_refresh_us_universe`.
"""
from __future__ import annotations

import io
import sys

import requests

from .config import repo_root

_NASDAQ_LISTED = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
_OTHER_LISTED = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"

# Heuristics to drop non-common-stock rows. We keep things conservative: better
# to score a few odd tickers than to silently drop names worth screening.
_DROP_NAME_SUFFIXES = (
    " ETF", " ETN", " FUND", " TRUST",
    " WARRANT", " WARRANTS",
    " UNIT", " UNITS",
    " RIGHT", " RIGHTS",
    " PREFERRED", " DEPOSITARY",
)


def _parse_nasdaqlisted(text: str) -> list[str]:
    """nasdaqlisted.txt columns: Symbol|Security Name|Market Category|Test Issue|...|ETF|..."""
    tickers: list[str] = []
    lines = text.splitlines()
    header = lines[0].split("|")
    idx = {name: i for i, name in enumerate(header)}
    for line in lines[1:]:
        if not line or line.startswith("File Creation Time"):
            continue
        parts = line.split("|")
        if len(parts) < len(header):
            continue
        symbol = parts[idx["Symbol"]].strip()
        name = parts[idx["Security Name"]].upper().strip()
        test = parts[idx["Test Issue"]].strip()
        etf = parts[idx.get("ETF", -1)].strip() if "ETF" in idx else "N"
        if not symbol or test == "Y" or etf == "Y":
            continue
        if any(suf in name for suf in _DROP_NAME_SUFFIXES):
            continue
        tickers.append(symbol)
    return tickers


def _parse_otherlisted(text: str) -> list[str]:
    """otherlisted.txt columns: ACT Symbol|Security Name|Exchange|...|ETF|...|Test Issue|NASDAQ Symbol"""
    tickers: list[str] = []
    lines = text.splitlines()
    header = lines[0].split("|")
    idx = {name: i for i, name in enumerate(header)}
    for line in lines[1:]:
        if not line or line.startswith("File Creation Time"):
            continue
        parts = line.split("|")
        if len(parts) < len(header):
            continue
        symbol = parts[idx.get("NASDAQ Symbol", idx.get("ACT Symbol", 0))].strip()
        name = parts[idx["Security Name"]].upper().strip()
        exchange = parts[idx.get("Exchange", 2)].strip()
        test = parts[idx["Test Issue"]].strip() if "Test Issue" in idx else "N"
        etf = parts[idx["ETF"]].strip() if "ETF" in idx else "N"
        # Exchange codes: N = NYSE, A = NYSE American, P = NYSE Arca (mostly ETFs)
        if not symbol or test == "Y" or etf == "Y":
            continue
        if exchange == "P":  # NYSE Arca is overwhelmingly ETFs/funds
            continue
        if any(suf in name for suf in _DROP_NAME_SUFFIXES):
            continue
        tickers.append(symbol)
    return tickers


def main() -> int:
    headers = {"User-Agent": "warren-bot/0.1"}
    print("Fetching nasdaqlisted.txt …")
    r1 = requests.get(_NASDAQ_LISTED, headers=headers, timeout=60)
    r1.raise_for_status()
    nasdaq = _parse_nasdaqlisted(r1.text)
    print(f"  {len(nasdaq)} NASDAQ tickers")

    print("Fetching otherlisted.txt …")
    r2 = requests.get(_OTHER_LISTED, headers=headers, timeout=60)
    r2.raise_for_status()
    other = _parse_otherlisted(r2.text)
    print(f"  {len(other)} other-exchange tickers")

    # Normalize: yfinance prefers dashes over dots for class shares (BRK.B -> BRK-B)
    all_tickers = sorted({t.replace(".", "-") for t in nasdaq + other if t})

    out = repo_root() / "config" / "universe_us.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(all_tickers) + "\n")
    print(f"Wrote {len(all_tickers)} tickers to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
