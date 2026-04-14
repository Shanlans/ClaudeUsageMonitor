"""Typer CLI entrypoint: snapshot / live / serve."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console

from claude_monitor.aggregator import UsageStore
from claude_monitor.config import PLAN_LIMITS, Plan, Settings, TokenWeights, load_limits
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
    limit_weekly_sonnet: int | None,
    limits_path: Path | None,
    claude_dir: Path | None,
    prices_path: Path | None,
    w_input: float | None,
    w_output: float | None,
    w_cache_create: float | None,
    w_cache_read: float | None,
) -> Settings:
    limits = load_limits(
        plan,
        h5=limit_5h,
        weekly=limit_weekly,
        weekly_sonnet=limit_weekly_sonnet,
        limits_path=limits_path,
    )
    weights = TokenWeights(
        input=w_input if w_input is not None else 1.0,
        output=w_output if w_output is not None else 1.0,
        cache_create=w_cache_create if w_cache_create is not None else 0.0,
        cache_read=w_cache_read if w_cache_read is not None else 0.0,
    )
    settings = Settings(plan=plan, limits=limits, weights=weights)
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
    limit_weekly_sonnet: Annotated[
        Optional[int],
        typer.Option(
            "--limit-weekly-sonnet",
            "--limit-weekly-opus",
            help="Override weekly Sonnet cap (tokens). `--limit-weekly-opus` kept as alias.",
        ),
    ] = None,
    limits_path: Annotated[
        Optional[Path], typer.Option("--limits", help="JSON file with {h5, weekly_total, weekly_sonnet}.")
    ] = None,
    claude_dir: Annotated[
        Optional[Path], typer.Option("--claude-dir", help="Path to ~/.claude/projects.")
    ] = None,
    prices_path: Annotated[Optional[Path], typer.Option("--prices", help="JSON price table override.")] = None,
    w_input: Annotated[
        Optional[float], typer.Option("--weight-input", help="Weight for input tokens (default 1.0).")
    ] = None,
    w_output: Annotated[
        Optional[float], typer.Option("--weight-output", help="Weight for output tokens (default 1.0).")
    ] = None,
    w_cache_create: Annotated[
        Optional[float],
        typer.Option(
            "--weight-cache-create",
            help="Weight for cache_creation tokens (default 0.0 — excluded from rate-limit billable).",
        ),
    ] = None,
    w_cache_read: Annotated[
        Optional[float],
        typer.Option("--weight-cache-read", help="Weight for cache_read tokens (default 0.0)."),
    ] = None,
) -> None:
    _state["settings"] = _build_settings(
        plan,
        limit_5h,
        limit_weekly,
        limit_weekly_sonnet,
        limits_path,
        claude_dir,
        prices_path,
        w_input,
        w_output,
        w_cache_create,
        w_cache_read,
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
        console.print(
            f"  {p.value:<7} 5h={l.h5:>10,}  weekly={l.weekly_total:>12,}  "
            f"weekly_sonnet={l.weekly_sonnet:>10,}"
        )
    console.print()
    console.print(f"[bold bright_green]resolved for --plan {settings.plan.value}:[/bold bright_green]")
    console.print(
        f"  5h={settings.limits.h5:,}  weekly={settings.limits.weekly_total:,}  "
        f"weekly_sonnet={settings.limits.weekly_sonnet:,}"
    )


# ---------------------------------------------------------------------------
# Calibration commands
# ---------------------------------------------------------------------------


@app.command()
def calibrate(
    no_send: Annotated[
        bool, typer.Option("--no-send", help="Skip sending the calibration prompt (measure external usage).")
    ] = False,
) -> None:
    """Send a known large prompt and measure the token delta for limit calibration.

    After the prompt completes, you'll be asked for the before/after usage %
    from Claude.ai's rate limit indicator so we can compute your real limit.
    """
    from claude_monitor.calibrate import run_calibrate

    run_calibrate(_state["settings"], skip_prompt=no_send)


@app.command("mark-limit")
def mark_limit(
    window: Annotated[
        str, typer.Option("--window", help="Which window you hit: 5h, weekly, or weekly_sonnet.")
    ] = "5h",
) -> None:
    """Record the current window total as the actual limit.

    Run this the moment you get rate-limited by Claude. It saves your current
    5h (or weekly) billable token total as the real cap for future reference.
    """
    from claude_monitor.calibrate import run_mark_limit

    run_mark_limit(_state["settings"], window=window)


@app.command()
def record(
    pct_5h: Annotated[
        Optional[float],
        typer.Option("--5h-pct", help="Claude.ai's current '5-hour session' % used."),
    ] = None,
    pct_weekly: Annotated[
        Optional[float],
        typer.Option("--weekly-pct", help="Claude.ai's current 'Weekly · All models' % used."),
    ] = None,
    pct_weekly_sonnet: Annotated[
        Optional[float],
        typer.Option("--weekly-sonnet-pct", help="Claude.ai's current 'Weekly · Sonnet only' % used."),
    ] = None,
) -> None:
    """Record a paired (snapshot, Claude.ai % values) observation.

    Go to Claude.ai → Settings → Usage, note the three percentages shown
    there, then run this command. It takes a snapshot at the same moment
    and stores both in ~/.config/claude-monitor/calibration.json. Over a
    few observations, it computes your real plan limits by regression.

    Pass values via flags, or run with no args to be prompted interactively.
    """
    from claude_monitor.calibrate import run_record

    run_record(
        _state["settings"],
        pct_5h=pct_5h,
        pct_weekly=pct_weekly,
        pct_weekly_sonnet=pct_weekly_sonnet,
    )


@app.command("calibration-status")
def calibration_status() -> None:
    """Show calibration history and current calibrated limits."""
    from claude_monitor.calibrate import show_calibration

    show_calibration(_state["settings"])


if __name__ == "__main__":
    app()
