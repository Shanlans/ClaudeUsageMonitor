"""Rich-based live TUI and one-shot snapshot rendering."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from claude_monitor.aggregator import UsageStore
from claude_monitor.config import POLL_INTERVAL, Settings
from claude_monitor.ingest import seed_and_tail
from claude_monitor.models import Snapshot, Window
from claude_monitor.tail import FileTailer

GREEN = "bright_green"
DIM_GREEN = "green4"
YELLOW = "yellow"
RED = "red"


def _color_for(pct: float) -> str:
    if pct >= 85:
        return RED
    if pct >= 60:
        return YELLOW
    return GREEN


def _format_eta(eta: timedelta | None) -> str:
    if eta is None:
        return "--"
    total_seconds = int(eta.total_seconds())
    if total_seconds < 0:
        return "--"
    days, rem = divmod(total_seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days >= 7:
        return ">7d"
    if days:
        return f"{days}d{hours:02d}h"
    if hours:
        return f"{hours}h{minutes:02d}m"
    return f"{minutes}m"


def _bar(pct: float, width: int = 30) -> str:
    # pct may exceed 100 (overage); the bar caps visually at `width`.
    fill_pct = max(0.0, min(100.0, pct))
    filled = int(round(width * fill_pct / 100))
    return "█" * filled + "░" * (width - filled)


def _fmt_num(n: int) -> str:
    if n >= 1_000_000:
        return f"{n/1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}k"
    return str(n)


def _window_row(label: str, w: Window, eta: timedelta | None) -> Text:
    color = _color_for(w.pct_used)
    bar = _bar(w.pct_used, 30)
    used = _fmt_num(w.billable_tokens)
    limit = _fmt_num(w.limit) if w.limit > 0 else "∞"
    text = Text(no_wrap=True, overflow="ellipsis")
    text.append(f"{label:<5}", style=DIM_GREEN)
    text.append(f"[{bar}] ", style=color)
    text.append(f"{w.pct_used:5.1f}% ", style=color)
    text.append(f"{used:>7}/{limit:<7} ", style=GREEN)
    text.append(f"{_format_eta(eta):>6}", style=DIM_GREEN)
    return text


def _render(snap: Snapshot, uptime: str) -> Panel:
    header = Text()
    header.append("[claude-monitor]", style=f"bold {GREEN}")
    header.append(f"  plan={snap.plan.value}", style=GREEN)
    header.append(f"  uptime={uptime}", style=DIM_GREEN)
    header.append("   ● LIVE", style=f"bold {GREEN} blink")

    bars = Group(
        _window_row("5h", snap.window_5h, snap.eta_5h),
        _window_row("wk", snap.window_weekly, snap.eta_weekly),
        _window_row("sonnet", snap.window_weekly_sonnet, snap.eta_weekly_sonnet),
    )

    burn_table = Table.grid(padding=(0, 2))
    burn_table.add_column(style=DIM_GREEN)
    burn_table.add_column(style=GREEN)
    burn_table.add_row("burn", f"{snap.burn_tokens_per_min:7.1f} tok/min")
    for fam in ("opus", "sonnet", "haiku", "other"):
        rate = snap.burn_by_family.get(fam, 0.0)
        if rate > 0 or fam in snap.window_5h.by_family:
            burn_table.add_row(fam, f"{rate:7.1f} tok/min")

    cost_table = Table(
        show_header=True,
        header_style=f"bold {GREEN}",
        border_style=DIM_GREEN,
        title_style=GREEN,
        title="by model (5h window)",
    )
    cost_table.add_column("family", style=DIM_GREEN)
    cost_table.add_column("records", justify="right")
    cost_table.add_column("input", justify="right")
    cost_table.add_column("output", justify="right")
    cost_table.add_column("cache_c", justify="right")
    cost_table.add_column("cache_r", justify="right")
    cost_table.add_column("cost", justify="right", style=GREEN)
    for fam in ("opus", "sonnet", "haiku", "other"):
        mb = snap.window_5h.by_family.get(fam)
        if mb is None:
            continue
        cost_table.add_row(
            fam,
            str(mb.records),
            _fmt_num(mb.input),
            _fmt_num(mb.output),
            _fmt_num(mb.cache_create),
            _fmt_num(mb.cache_read),
            f"${mb.cost_usd:.4f}",
        )

    footer = Text()
    footer.append("total cost (retained): ", style=DIM_GREEN)
    footer.append(f"${snap.total_cost_usd:.4f}", style=f"bold {GREEN}")
    footer.append(f"   sessions: {len(snap.active_session_ids)}", style=DIM_GREEN)

    body = Group(header, Text(""), bars, Text(""), burn_table, Text(""), cost_table, Text(""), footer)

    return Panel(
        body,
        border_style=GREEN,
        title=f"[bold {GREEN}]claude usage monitor[/bold {GREEN}]",
        title_align="left",
        padding=(1, 2),
    )


def print_snapshot(console: Console, snap: Snapshot) -> None:
    console.print(_render(snap, uptime="0m"))


def run_live(settings: Settings) -> None:
    console = Console()
    store = UsageStore()
    tailer = FileTailer()
    started = datetime.now(timezone.utc)

    seed_and_tail(store, tailer, settings.claude_dir)
    snap = store.snapshot(settings)

    with Live(_render(snap, uptime="0m"), console=console, refresh_per_second=2, screen=False) as live:
        try:
            while True:
                time.sleep(POLL_INTERVAL)
                seed_and_tail(store, tailer, settings.claude_dir)
                snap = store.snapshot(settings)
                elapsed = datetime.now(timezone.utc) - started
                total = int(elapsed.total_seconds())
                h, r = divmod(total, 3600)
                m = r // 60
                uptime = f"{h}h{m:02d}m" if h else f"{m}m"
                live.update(_render(snap, uptime))
        except KeyboardInterrupt:
            pass
