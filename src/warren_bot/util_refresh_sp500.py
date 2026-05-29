"""Refresh config/universe_sp500.txt from Wikipedia.

Run quarterly: `python -m warren_bot.util_refresh_sp500`.
"""
from __future__ import annotations

import sys

import io

import pandas as pd
import requests

from .config import repo_root


def main() -> int:
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    resp = requests.get(url, headers={"User-Agent": "warren-bot/0.1"}, timeout=30)
    resp.raise_for_status()
    tables = pd.read_html(io.StringIO(resp.text))
    df = tables[0]
    col = "Symbol" if "Symbol" in df.columns else df.columns[0]
    tickers = sorted({str(t).replace(".", "-").strip().upper() for t in df[col]})
    out = repo_root() / "config" / "universe_sp500.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(tickers) + "\n")
    print(f"Wrote {len(tickers)} S&P 500 tickers to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
