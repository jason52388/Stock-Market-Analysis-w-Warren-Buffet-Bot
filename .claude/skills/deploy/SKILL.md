---
name: deploy
description: >-
  Deploy the warren-bot to the Hostinger VPS and trigger fresh screener runs.
  Use when the user asks to deploy, ship, release, push to production, refresh
  the dashboard/site, run the bot on the server, or check the deployed site.
  Encodes the CODE-ONLY deploy model and the DECOUPLED data-refresh paths so a
  deploy never accidentally tries to run the multi-hour screen inline.
---

# Deploying warren-bot

The bot lives on a Hostinger VPS, runs in Docker (a `bot` container + a `caddy`
reverse proxy), screens the stock universe weekly via in-container cron, emails
a digest, syncs Notion, and publishes `out/dashboard.html` + an archive at
`https://$SITE_HOSTNAME/`. Full reference: `DEPLOY.md`.

## The one rule that matters: deploy ships CODE, not DATA

A full yfinance screen of ~5,673 tickers takes **hours**. It must never run
inline with a deploy or any SSH/GitHub-job timeout — that produces a
half-finished, alphabetically-truncated dashboard. Data refresh is **decoupled**
to the only two paths with no timeout:

1. **Weekly cron**, in-container (Sunday 18:00, `deploy/crontab`).
2. **The "Run bot now" workflow** (`.github/workflows/run-now.yml`), which kicks
   off a *detached* run (`docker compose exec -d`) protected by a `/tmp/warren-run.lock`.

The site keeps serving the **last complete run** until a new one finishes.
`build-index.sh` (cheap, relists the archive) is the only data-touching step
safe to run on every deploy.

## Deploying code

Deploy is automatic on push to `main` via `.github/workflows/deploy.yml`
(SSH → ensure clean clone → write `.env` from secrets → `docker compose up -d --build`
→ `build-index.sh`).

- **Normal flow:** merge/push to `main`, then watch the **Deploy to Hostinger VPS**
  workflow in the Actions tab. It finishes in well under its 45m budget because
  it does not screen.
- **Manual re-deploy without a code change:** trigger the deploy workflow via
  `workflow_dispatch`.
- Required GitHub secrets are documented at the top of `deploy.yml` and in
  `DEPLOY.md` (connection: `VPS_*`; app config: `SITE_HOSTNAME`, `GMAIL_*`,
  `NOTION_*`; optional sources: `SEC_USER_AGENT`, `FMP_API_KEY`, `FINNHUB_API_KEY`).
  Note the mapping quirk: the repo secret `GMAIL_APP_SECRET` → env `GMAIL_APP_PASSWORD`.

## Refreshing the dashboard with fresh data

Trigger the **Run bot now** workflow (`workflow_dispatch`). Optional `limit`
input: blank = full universe (hours), `30` = quick smoke test. The run is
detached and continues after the workflow exits — re-running while one is in
flight is refused by the lock file. Watch progress via the live site or
`out/warren-run.log` in the container.

## Other workflows

- `status.yml` / `diagnose.yml` — inspect the running VPS containers/site.
- `restart-caddy.yml` — bounce the reverse proxy (TLS / 404 issues).

## Verifying a deploy

After the deploy workflow is green: the site serves; `docker compose ps` on the
VPS shows fresh timestamps. A 404 on `/dashboard.html` just means no complete
run exists yet — trigger **Run bot now**. See `DEPLOY.md` › Troubleshooting for
the cron-vs-env Gmail gotcha and Caddy/HTTPS checks.

> GitHub operations here use the `mcp__github__*` tools (no `gh` CLI). Do not
> create a PR unless the user asks.
