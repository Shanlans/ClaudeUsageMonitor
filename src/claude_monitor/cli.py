"""Typer CLI entrypoint: snapshot / live / serve."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console

from claude_monitor.aggregator import UsageStore
from claude_monitor.config import PLAN_LIMITS, Plan, Settings, load_limits
from claude_monitor.ingest import seed_and_tail
from claude_monitor.tail import FileTailer

app = typer.Typer(
    help="Monitor your claude.ai Pro/Max usage (5h + weekly rolling windows).",
    no_args_is_help=True,
    add_completion=False,
)

_state: dict = {}


def _build_settings(
    plan: Plan,
    limit_5h: int | None,
    limit_weekly: int | None,
    limit_weekly_opus: int | None,
    limits_path: Path | None,
    claude_dir: Path | None,
    prices_path: Path | None,
) -> Settings:
    limits = load_limits(
        plan,
        h5=limit_5h,
        weekly=limit_weekly,
        weekly_opus=limit_weekly_opus,
        limits_path=limits_path,
    )
    settings = Settings(plan=plan, limits=limits)
    if claude_dir is not None:
        settings.claude_dir = claude_dir
    if prices_path is not None:
        settings.prices_path = prices_path
    return settings


@app.callback()
def _root(
    ctx: typer.Context,
    plan: Annotated[Plan, typer.Option("--plan", help="Subscription plan.")] = Plan.max5,
    limit_5h: Annotated[Optional[int], typer.Option("--limit-5h", help="Override 5h cap (tokens).")] = None,
    limit_weekly: Annotated[Optional[int], typer.Option("--limit-weekly", help="Override weekly cap (tokens).")] = None,
    limit_weekly_opus: Annotated[
        Optional[int], typer.Option("--limit-weekly-opus", help="Override weekly Opus cap (tokens).")
    ] = None,
    limits_path: Annotated[
        Optional[Path], typer.Option("--limits", help="JSON file with {h5, weekly_total, weekly_opus}.")
    ] = None,
    claude_dir: Annotated[
        Optional[Path], typer.Option("--claude-dir", help="Path to ~/.claude/projects.")
    ] = None,
    prices_path: Annotated[Optional[Path], typer.Option("--prices", help="JSON price table override.")] = None,
) -> None:
    _state["settings"] = _build_settings(
        plan, limit_5h, limit_weekly, limit_weekly_opus, limits_path, claude_dir, prices_path
    )


@app.command()
def snapshot() -> None:
    """Print a one-shot usage snapshot and exit."""
    from claude_monitor.tui import print_snapshot

    settings: Settings = _state["settings"]
    console = Console()
    store = UsageStore()
    tailer = FileTailer()
    seed_and_tail(store, tailer, settings.claude_dir)
    snap = store.snapshot(settings)
    print_snapshot(console, snap)


@app.command()
def live() -> None:
    """Launch the live rich-based TUI, refreshing every ~2s."""
    from claude_monitor.tui import run_live

    settings: Settings = _state["settings"]
    run_live(settings)


@app.command()
def serve(
    host: Annotated[str, typer.Option(help="Bind address.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Port.")] = 8765,
) -> None:
    """Start the local web dashboard."""
    import uvicorn

    from claude_monitor.web.server import build_app

    settings: Settings = _state["settings"]
    web_app = build_app(settings)
    uvicorn.run(web_app, host=host, port=port, log_level="info")


@app.command("show-limits")
def show_limits() -> None:
    """Print the built-in default limits per plan and the currently resolved limits."""
    console = Console()
    settings: Settings = _state["settings"]
    console.print("[bold bright_green]default plan limits (approximate):[/bold bright_green]")
    for p, l in PLAN_LIMITS.items():
        console.print(f"  {p.value:<7} 5h={l.h5:>10,}  weekly={l.weekly_total:>12,}  weekly_opus={l.weekly_opus:>10,}")
    console.print()
    console.print(f"[bold bright_green]resolved for --plan {settings.plan.value}:[/bold bright_green]")
    console.print(
        f"  5h={settings.limits.h5:,}  weekly={settings.limits.weekly_total:,}  "
        f"weekly_opus={settings.limits.weekly_opus:,}"
    )


if __name__ == "__main__":
    app()
