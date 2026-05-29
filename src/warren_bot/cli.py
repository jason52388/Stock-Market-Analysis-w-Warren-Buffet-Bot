"""Warren Bot CLI."""
from __future__ import annotations

import logging
import os
from pathlib import Path

import click

from .config import load_settings, repo_root
from .pipeline import (
    Pick,
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
    picks = run_universe(settings, limit=limit, force_refresh=force_refresh, sample=sample)
    strong, angles, partial = split_picks(picks, settings)
    surfaced = strong + angles + partial
    click.echo(f"Scored {len(picks)} tickers: {len(strong)} strong, "
               f"{len(angles)} angles, {len(partial)} partial")

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
        briefing = Briefing(generated_at=__import__("datetime").datetime.utcnow())
    else:
        click.echo("Building weekly briefing…")
        briefing = build_briefing()
        click.echo(f"  {briefing.total_articles} articles across "
                   f"{len(briefing.topics)} topics")

    # Hedge fund holdings/buys/sells from dataroma + Berkshire's full portfolio.
    hedge_views = {}
    brk_portfolio = None
    if not skip_news:
        click.echo("Fetching hedge fund activity (dataroma)…")
        from .data.cache import Cache
        from .hedge_funds.dataroma import (
            fetch_hedge_fund_views,
            fetch_manager_portfolio,
        )
        data_cfg = settings["data"]
        hf_cache = Cache(repo_root() / data_cfg["cache_path"],
                         ttl_seconds=int(data_cfg["cache_ttl_hours"]) * 3600)
        hedge_views = fetch_hedge_fund_views(hf_cache, max_rows=50)
        click.echo(f"  holdings={len(hedge_views.get('holdings').rows if hedge_views.get('holdings') else [])} "
                   f"buys={len(hedge_views.get('buys').rows if hedge_views.get('buys') else [])} "
                   f"sells={len(hedge_views.get('sells').rows if hedge_views.get('sells') else [])}")
        brk_portfolio = fetch_manager_portfolio(hf_cache, "BRK")
        if brk_portfolio:
            click.echo(f"  berkshire={len(brk_portfolio.positions)} positions "
                       f"({len(brk_portfolio.active_positions)} active)")

    # Blended recommendations (Buffett ∩ hedge fund signal)
    from .recommendations import build_recommendations
    recommendations = build_recommendations(surfaced, hedge_views)
    click.echo(f"Built {len(recommendations)} blended recommendations")

    # Build KPI rows for the Market KPIs tab (all screened tickers, errored ones excluded).
    from .dashboard.render import build_kpi_rows, render_dashboard
    kpi_rows = build_kpi_rows([p for p in picks if not p.score.error])
    click.echo(f"Built KPI table: {len(kpi_rows)} rows")

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
    )
    dash_path.write_text(dash_html)
    click.echo(f"Wrote dashboard: {dash_path}")

    if skip_delivery:
        return

    email_cfg = settings["delivery"]["email"]
    if email_cfg.get("enabled") and os.environ.get("GMAIL_APP_PASSWORD"):
        from .delivery.email_render import render_summary
        from .delivery.email_send import send_email
        html = render_summary(strong, angles, partial,
                              total_scored=len(picks),
                              briefing_count=briefing.total_articles)
        subject = (f"Buffett Bot — {len(strong)} strong, {len(angles)} angles, "
                   f"{len(partial)} partial, {briefing.total_articles} briefing")
        send_email(subject, html, email_cfg, attachments=[dash_path])
        click.echo("Email sent (with dashboard attached)")
    else:
        click.echo("Email delivery skipped (disabled or no GMAIL_APP_PASSWORD)")

    notion_cfg = settings["delivery"]["notion"]
    if notion_cfg.get("enabled") and os.environ.get("NOTION_API_KEY") \
            and notion_cfg.get("database_id"):
        from .delivery.notion_sync import sync_picks
        sync_picks(strong + angles, notion_cfg)
        click.echo("Notion synced")
    else:
        click.echo("Notion delivery skipped (disabled or missing config)")


if __name__ == "__main__":
    cli()
