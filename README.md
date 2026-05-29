# Warren Buffett / Berkshire Hathaway Bot

A weekly stock screener that ranks S&P 500 + major international ADRs against
Buffett/Munger criteria, emails you a digest with heat-map cards + thesis for
each pick, and mirrors the results to a Notion database.

> Quantitative proxies, not Buffett-quality qualitative judgment. Treat the
> output as a watchlist generator, not a buy list.

## How scoring works

Each ticker is graded 0–100 on five dimensions, then weighted into a total:

| Dimension | Weight | Criteria |
|---|---|---|
| Moat & Profitability | 30% | ROE, ROIC, gross margin, net margin |
| Financial Strength | 20% | Debt/Equity, interest coverage, current ratio |
| Consistency | 20% | Profitable years, revenue CAGR, EPS CAGR, FCF+ years |
| Valuation / Margin of Safety | 20% | FCF yield, P/E vs history, owner-earnings DCF |
| Capital Allocation | 10% | Shareholder yield, share-count CAGR |

- **≥ 75** → "strong match"
- **60–74** → "interesting angle" (great on some dimensions, missing others)
- **45–59** → "partial match" (weaker fit, kept around so the dashboard isn't empty when nothing clears 60)
- **< 45** → not surfaced

Thresholds and weights live in `config/settings.yaml` — adjust freely.

## Quick start

```bash
# Install
uv venv --python 3.12 && uv pip install -e .

# Score one ticker
uv run warren-bot screen KO

# Quick local run (random 100-ticker sample, no email/Notion)
uv run warren-bot run --limit 100 --skip-delivery
open out/dashboard.html      # interactive dashboard with Picks + Briefing tabs

# Quick run, skip news/briefing (fastest)
uv run warren-bot run --limit 100 --skip-delivery --skip-news

# Full universe (~5,400 US stocks + ADRs, ~30–90 min depending on cache)
uv run warren-bot run --skip-delivery
```

`--limit N` defaults to **random sample** (deterministic seed). Pass `--head` to
take the alphabetical head instead.

## Outputs

Every run writes two files to `out/`:

- **`dashboard.html`** — single self-contained interactive page. Four tabs:
  - **Recommended** (default) — blended cross-reference of the Buffett screen
    and the dataroma 13F data. Each row shows a *composite score* (quant +
    held-by-managers bonus + accumulation bonus − distribution penalty) and a
    *tier badge*: **consensus** (passed screen, held by many managers),
    **accumulating** (passed screen, actively being bought), **caution**
    (passed screen but smart money is exiting), or **quant-only**. Reason tags
    spell out each signal: "Held by N super-investors", "5 buying last qtr", etc.
    Filterable by tier, sector, min composite, search.
  - **Buffett picks** — filterable by min score, sector, moat/strength/valuation
    thresholds, or text search. Each pick has a heat-map (green/yellow/red per
    criterion) and an inner-tabbed content area: **About** (full business
    description), **Buffett Analysis** (thesis: moat, balance sheet, growth,
    valuation, watch-outs), **News** (recent Yahoo Finance items).
  - **Weekly Briefing** — past-7-day tech / AI / industry / macro / crypto
    articles from Reuters, Bloomberg, WSJ, FT, NYT, The Economist, MIT Tech
    Review, Ars Technica, The Verge, SemiAnalysis, Stratechery, and the AI
    lab blogs (Anthropic, OpenAI, Hugging Face). Filterable by topic, source,
    tier-1-only, and text search.
  - **Hedge Funds** — aggregated 13F holdings from
    [dataroma.com](https://www.dataroma.com/m/grid.php)'s tracked super-investors
    (Buffett, Ackman, Klarman, Greenblatt, Burry, etc.). Three sections:
    *Largest holdings* (by # of super-investors owning), *Biggest accumulation*
    (most-bought last quarter), *Biggest distribution* (most-sold last quarter).
    A "picked" pill marks names that also passed our Buffett screen — filter
    "Overlap with my picks" to see only those. 13F data updates quarterly so
    this view is cached for a week.
- **`picks.csv`** — full ranked output with dimension scores and headline metrics.

When email is enabled, the dashboard is attached as `dashboard.html`; the email
body is just a short summary table.

## Delivery setup

Two delivery channels run in the same pipeline. Both are optional; either can
be disabled in `config/settings.yaml`.

### Email (Gmail SMTP)

The CLI sends via Gmail SMTP using an **app password** (MCP can't run inside GH
Actions cron — app password is the standard headless path).

1. Enable 2FA on the Google account: <https://myaccount.google.com/security>.
2. Generate an app password at <https://myaccount.google.com/apppasswords>.
3. Set env vars (locally in a `.env` or in GH Actions secrets):
   - `GMAIL_FROM` — the sending Gmail address
   - `GMAIL_TO` — where to deliver the digest (can be the same)
   - `GMAIL_APP_PASSWORD` — the 16-char app password

### Notion

1. Create an internal integration at
   <https://www.notion.so/profile/integrations> and copy the secret.
2. Create a database in Notion with these columns (exact names):

   | Name | Type |
   |---|---|
   | Ticker | Title |
   | Name | Text |
   | Sector | Select |
   | Total Score | Number |
   | Moat | Number |
   | Strength | Number |
   | Consistency | Number |
   | Valuation | Number |
   | CapAlloc | Number |
   | Price | Number |
   | FCF Yield % | Number |
   | MoS % | Number |
   | Last Updated | Date |
   | Thesis | Text |

3. Share the database with your integration (… → Connections → add).
4. Grab the database ID from the URL (the 32-char string before the `?`).
5. Set env vars:
   - `NOTION_API_KEY` — the integration secret
   - `NOTION_DATABASE_ID` — the 32-char ID

## Scheduling

`.github/workflows/weekly.yml` runs every Sunday 22:00 UTC and can also be
triggered on-demand from the Actions tab (workflow_dispatch).

Required GH Actions secrets:
`GMAIL_FROM`, `GMAIL_TO`, `GMAIL_APP_PASSWORD`,
`NOTION_API_KEY`, `NOTION_DATABASE_ID`.

## Universe

The bot reads tickers from every file listed in `settings.yaml > universe.files`
(merged + deduped). Defaults:

- `config/universe_us.txt` — **all ~5,400 US common stocks** from the
  authoritative NASDAQ symbol directory (refresh quarterly via
  `python -m warren_bot.util_refresh_us_universe`). Pre-filtered to common stocks
  only — ETFs, preferred shares, warrants, units, and test issues are dropped.
- `config/universe_adrs.txt` — ~150 major international ADRs.
- `config/watchlist.txt` — manual picks (seeded with Berkshire holdings + classic
  Buffett-style names). Edit freely.
- `config/universe_sp500.txt` — S&P 500 list (alternative narrower universe;
  refresh with `python -m warren_bot.util_refresh_sp500`). Comment out
  `universe_us.txt` in settings and uncomment this file to narrow scope.

**Market-cap pre-filter:** `settings.yaml > data.min_market_cap_usd` (default
$300M) skips deep-fetch for micro-caps, dropping ~70% of the all-US universe's
runtime. Set to `0` to disable.

## Weekly briefing sources

`config/briefing_sources.yaml` lists every RSS feed used for the briefing tab.
Tiers: 1 = institutional financial press (Reuters/Bloomberg/WSJ/FT/NYT/Economist),
2 = high-quality tech (MIT TR, Ars, Verge, Wired, SemiAnalysis, Stratechery),
3 = AI lab blogs (Anthropic, OpenAI, Hugging Face), 4 = community (HN). Add or
remove feeds as you like; topic keyword sets are defined in the same file.

## Notes & known limitations

- **yfinance only ships 4–5 years of annual statements free.** Per-year metrics
  ("Profitable yrs of N") scale to the available window — they're not always
  the 10-year set Buffett would look at. Upgrade path: paid data API (FMP /
  EODHD).
- **Financial-sector stocks** (banks, insurers) score weak on Strength because
  debt/equity and interest coverage don't apply the usual way. A sector-aware
  scoring path is on the future-add list.
- **P/E vs 10y median** is approximated from monthly price history × current
  EPS, not historical EPS — directional only.
- **Owner-earnings DCF** assumes capex ≈ maintenance capex (no split). Growth
  capped at 10%, terminal 2.5%, discount 10%.

## Project layout

```
src/warren_bot/
  data/        # yfinance fetcher + SQLite cache
  analysis/    # ratios, growth, valuation, scorer
  thesis/      # template-based narrative
  delivery/    # email render+send, Notion sync
  cli.py       # `warren-bot screen | run`
  pipeline.py  # orchestrator
config/        # YAML settings + universe txt files
.github/workflows/weekly.yml
```
