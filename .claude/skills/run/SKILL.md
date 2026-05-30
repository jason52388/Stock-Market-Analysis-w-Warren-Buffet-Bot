---
name: run
description: >-
  Run the warren-bot screener locally and preview the generated dashboard. Use
  when the user wants to run the bot, do a quick screen, generate or look at the
  dashboard HTML, or test changes to scoring/rendering before deploying. Covers
  both the fast local CLI loop and the docker-compose path.
---

# Running warren-bot locally

The CLI entrypoint is `warren-bot` (defined in `pyproject.toml` → `cli.py`).
The SessionStart hook syncs the venv, so `warren-bot` is on PATH; otherwise run
via `uv run warren-bot ...`.

## Fast iteration loop (recommended)

A full screen hits ~5,673 tickers and takes hours — **always cap it** while
developing:

```bash
# Quick smoke screen, skip email/Notion delivery, write the dashboard locally.
uv run warren-bot run --limit 30 --skip-delivery --dashboard-out out/dashboard.html
```

Useful `run` flags (see `warren-bot run --help`):
- `--limit N` — cap the universe (omit for the full, slow run).
- `--skip-delivery` — don't send the email or sync Notion.
- `--force-refresh` — bypass the yfinance cache.
- `--skip-news` / `--with-news` — toggle the news briefing.
- `--csv-out PATH` / `--dashboard-out PATH` — where to write outputs.

Screen a single ticker without the universe:

```bash
uv run warren-bot screen AAPL
```

## Previewing the dashboard

There's a launch config in `.claude/launch.json` named **dashboard-preview** —
it serves `out/` on port 8000:

```bash
.venv/bin/python -m http.server 8000 --directory out
```

Then open the served `dashboard.html`.

## Full stack via Docker

To exercise the production layout (bot + Caddy) locally, use the
**docker-compose** launch config or:

```bash
docker compose up --build
```

This mirrors the VPS: Caddy on :80 fronting the bot's `out/`. Note compose does
not auto-run a screen — run one inside the container the same way cron does:
`docker compose exec bot warren-bot run --limit 30`.

## Tests / lint before committing

```bash
uv run pytest          # test suite (uses pytest-vcr cassettes)
uv run ruff check .    # lint
```
