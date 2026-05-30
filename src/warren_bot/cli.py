"""Warren Bot CLI."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import click

from .config import load_settings, repo_root
from .pipeline import (
    Pick,
    build_cache,
    gather_stock_news,
    run_universe,
    screen_one,
    split_picks,
    write_csv,
)


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _print_pick(p: Pick) -> None:
    s = p.score
    click.echo("")
    click.echo(click.style(f"{s.ticker}  {s.name}  ({s.sector})", bold=True))
    click.echo(f"Total score: {s.total}/100" + (f"   ERROR: {s.error}" if s.error else ""))
    for d in s.dimensions:
        click.echo(f"  {d.name}: {d.score:.1f}")
        for c in d.cells:
            colors = {"hit": "green", "marginal": "yellow", "miss": "red", "na": "white"}
            tag = click.style(f"[{c.status}]", fg=colors[c.status])
            click.echo(f"    {tag} {c.label}: {c.display()}  (target {c.target}{c.unit})")
    click.echo("")
    click.echo(p.thesis.as_markdown())


def _exclusion_summary(picks: list[Pick]) -> "dict[str, int]":
    """Count excluded (errored) picks by reason so a run reports WHY names were
    dropped — chiefly so a high 'incomplete data' count flags Yahoo throttling."""
    from collections import Counter
    cats: Counter[str] = Counter()
    for p in picks:
        e = p.score.error or ""
        if not e:
            continue
        if e.startswith("incomplete data"):
            cats["incomplete data"] += 1
        elif "below min market cap" in e and "mcap=None" in e:
            cats["missing market cap (throttled)"] += 1
        elif "below min market cap" in e:
            cats["below min market cap"] += 1
        else:
            cats["other error"] += 1
    return dict(cats)


def _data_quality_warning(picks: list[Pick]) -> str | None:
    if not picks:
        return None
    errored = [p for p in picks if p.score.error]
    clean = len(picks) - len(errored)
    mcap_none = [
        p for p in errored
        if "below min market cap" in (p.score.error or "")
        and "mcap=None" in (p.score.error or "")
    ]
    incomplete = [p for p in errored if (p.score.error or "").startswith("incomplete data")]

    floor = max(25, int(len(picks) * 0.10))
    if len(mcap_none) >= floor:
        return (
            "WARNING: This run has "
            f"{len(mcap_none)}/{len(picks)} tickers with missing Yahoo market cap "
            f"(clean scores: {clean}). The picks may be alphabet-biased/incomplete; "
            "rerun after the transient cache expires or use --force-refresh before "
            "trusting the dashboard."
        )
    # Incomplete-statement exclusions are expected at a low rate, but a high rate
    # means Yahoo throttled many per-statement pulls this run — the surfaced set
    # is then a thin, possibly biased slice. Flag it so the run isn't trusted.
    if len(incomplete) >= floor:
        return (
            "WARNING: This run dropped "
            f"{len(incomplete)}/{len(picks)} tickers for incomplete statements "
            f"(complete scores: {clean}). That's likely heavy Yahoo throttling, "
            "so the surfaced picks are a thin slice — re-run (incomplete fetches "
            "retry automatically next run) or use --force-refresh before trusting it."
        )
    return None


@click.group()
@click.option("--verbose", is_flag=True, default=False)
@click.pass_context
def cli(ctx: click.Context, verbose: bool) -> None:
    """Warren Buffett / Berkshire Hathaway stock screening bot."""
    _setup_logging(verbose)
    ctx.ensure_object(dict)
    ctx.obj["settings"] = load_settings()


@cli.command()
@click.argument("ticker")
@click.pass_context
def screen(ctx: click.Context, ticker: str) -> None:
    """Score a single ticker and print a breakdown."""
    pick = screen_one(ticker.upper(), ctx.obj["settings"])
    if pick is None:
        click.echo(f"Could not screen {ticker}")
        raise SystemExit(1)
    _print_pick(pick)


@cli.command()
@click.option("--limit", type=int, default=None, help="Cap universe (debug)")
@click.option("--sample/--head", default=True,
              help="With --limit: sample randomly (default) vs head-of-list")
@click.option("--force-refresh", is_flag=True, default=False)
@click.option("--skip-delivery", is_flag=True, default=False,
              help="Don't send email or sync Notion")
@click.option("--csv-out", type=click.Path(path_type=Path),
              default=Path("out/picks.csv"), show_default=True)
@click.option("--dashboard-out", type=click.Path(path_type=Path),
              default=Path("out/dashboard.html"), show_default=True)
@click.option("--skip-news/--with-news", default=False,
              help="Skip per-stock news + briefing (faster, lower-fidelity output)")
@click.pass_context
def run(
    ctx: click.Context,
    limit: int | None,
    sample: bool,
    force_refresh: bool,
    skip_delivery: bool,
    csv_out: Path,
    dashboard_out: Path,
    skip_news: bool,
) -> None:
    """Run the full pipeline: screen → score → fetch news → render dashboard → deliver."""
    settings = ctx.obj["settings"]

    # One shared Cache for the entire run — used by the screener, dataroma,
    # and the BRK portfolio fetch. Prune stale entries up front so the SQLite
    # file doesn't grow unbounded across weekly runs.
    cache = build_cache(settings)
    pruned = cache.prune()
    if pruned:
        click.echo(f"Cache prune: removed {pruned} stale entries")

    picks = run_universe(
        settings, limit=limit, force_refresh=force_refresh, sample=sample, cache=cache
    )
    strong, angles, partial = split_picks(picks, settings)
    surfaced = strong + angles + partial
    complete = sum(1 for p in picks if not p.score.error)
    click.echo(f"Scored {complete}/{len(picks)} tickers with complete data: "
               f"{len(strong)} strong, {len(angles)} angles, {len(partial)} partial")
    if excl := _exclusion_summary(picks):
        click.echo("Excluded: " + ", ".join(
            f"{n} {reason}" for reason, n in sorted(excl.items(), key=lambda kv: -kv[1])))
    if warning := _data_quality_warning(picks):
        click.echo(click.style(warning, fg="yellow"), err=True)

    out_csv = repo_root() / csv_out if not csv_out.is_absolute() else csv_out
    write_csv(picks, out_csv)
    click.echo(f"Wrote {out_csv}")

    # Gather news for surfaced picks + the topical weekly briefing.
    stock_news = {}
    if not skip_news and surfaced:
        click.echo(f"Fetching news for {len(surfaced)} surfaced picks…")
        stock_news = gather_stock_news(surfaced)

    from .news.briefing import Briefing, build_briefing
    if skip_news:
        briefing = Briefing(generated_at=datetime.now(timezone.utc))
    else:
        click.echo("Building weekly briefing…")
        briefing = build_briefing()
        click.echo(f"  {briefing.total_articles} articles across "
                   f"{len(briefing.topics)} topics")

    # Hedge fund holdings/buys/sells from dataroma + Berkshire's full portfolio.
    # Reuses the shared `cache` instance built above — one SQLite connection
    # for the whole run.
    hedge_views = {}
    brk_portfolio = None
    if not skip_news:
        click.echo("Fetching hedge fund activity (dataroma)…")
        from .hedge_funds.dataroma import (
            fetch_hedge_fund_views,
            fetch_manager_portfolio,
        )
        hedge_views = fetch_hedge_fund_views(cache, max_rows=50)
        holdings = hedge_views.get("holdings")
        buys = hedge_views.get("buys")
        sells = hedge_views.get("sells")
        click.echo(
            f"  holdings={len(holdings.rows) if holdings else 0} "
            f"buys={len(buys.rows) if buys else 0} "
            f"sells={len(sells.rows) if sells else 0}"
        )
        brk_portfolio = fetch_manager_portfolio(cache, "BRK")
        if brk_portfolio:
            click.echo(f"  berkshire={len(brk_portfolio.positions)} positions "
                       f"({len(brk_portfolio.active_positions)} active)")

    # Blended recommendations (Buffett ∩ hedge fund signal)
    from .recommendations import build_recommendations
    recommendations = build_recommendations(surfaced, hedge_views)
    click.echo(f"Built {len(recommendations)} blended recommendations")

    # Build KPI rows for the Market KPIs tab (all screened tickers, errored ones excluded).
    from .dashboard.render import build_cockpit_data, build_kpi_rows, render_dashboard
    kpi_rows = build_kpi_rows([p for p in picks if not p.score.error])
    click.echo(f"Built KPI table: {len(kpi_rows)} rows")

    # Cockpit feed: every surfaced pick gets a fully-populated payload (KPIs,
    # dim scores, thesis, news, recommendation/composite if present).
    cockpit_data = build_cockpit_data(surfaced, recommendations, stock_news)
    click.echo(f"Built cockpit data: {len(cockpit_data)} tickers")

    # Render dashboard.html (always)
    dash_path = repo_root() / dashboard_out if not dashboard_out.is_absolute() else dashboard_out
    dash_path.parent.mkdir(parents=True, exist_ok=True)
    dash_html = render_dashboard(
        strong, angles, briefing, stock_news, hedge_views,
        recommendations,
        brk_portfolio=brk_portfolio,
        kpi_rows=kpi_rows,
        cache_ttl_hours=int(settings["data"]["cache_ttl_hours"]),
        partial=partial,
        cockpit_data=cockpit_data,
    )
    dash_path.write_text(dash_html)
    click.echo(f"Wrote dashboard: {dash_path}")

    if skip_delivery:
        return

    # Delivery failures must NOT abort the run — the cron line chains an archive
    # snapshot + index regen after this, and a Gmail/Notion hiccup shouldn't
    # forfeit the week's archive. Each channel is independently try/excepted.
    email_cfg = settings["delivery"]["email"]
    if email_cfg.get("enabled") and os.environ.get("GMAIL_APP_PASSWORD"):
        try:
            from .delivery.email_render import render_summary
            from .delivery.email_send import send_email
            html = render_summary(strong, angles, partial,
                                  total_scored=len(picks),
                                  briefing_count=briefing.total_articles)
            subject = (f"Buffett Bot — {len(strong)} strong, {len(angles)} angles, "
                       f"{len(partial)} partial, {briefing.total_articles} briefing")
            send_email(subject, html, email_cfg, attachments=[dash_path])
            click.echo("Email sent (with dashboard attached)")
        except Exception as e:
            click.echo(f"Email delivery FAILED: {type(e).__name__}: {e}", err=True)
            logging.getLogger(__name__).exception("email send failed")
    else:
        click.echo("Email delivery skipped (disabled or no GMAIL_APP_PASSWORD)")

    notion_cfg = settings["delivery"]["notion"]
    if notion_cfg.get("enabled") and os.environ.get("NOTION_API_KEY") \
            and notion_cfg.get("database_id"):
        try:
            from .delivery.notion_sync import sync_picks
            # Sync everything we'd show in the email — partial picks land in
            # Notion too so the DB is a complete record of what was surfaced.
            sync_picks(strong + angles + partial, notion_cfg)
            click.echo("Notion synced")
        except Exception as e:
            click.echo(f"Notion sync FAILED: {type(e).__name__}: {e}", err=True)
            logging.getLogger(__name__).exception("notion sync failed")
    else:
        click.echo("Notion delivery skipped (disabled or missing config)")


if __name__ == "__main__":
    cli()
