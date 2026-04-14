"""Calibration tool: send a known prompt, measure the token delta, compute real limits."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from claude_monitor.aggregator import UsageStore
from claude_monitor.config import PlanLimits, Settings
from claude_monitor.ingest import seed_and_tail
from claude_monitor.models import Snapshot
from claude_monitor.tail import FileTailer

# ---------------------------------------------------------------------------
# Calibration prompt: configurable padding + ask for ~1k output tokens
# Size is tunable via CLAUDE_MONITOR_PADDING_SENTENCES (default 500 ≈ 5k tokens).
# Reduce if you hit argv limits or the claude CLI crashes on long input.
# ---------------------------------------------------------------------------
_PADDING_SENTENCE = "The quick brown fox jumps over the lazy dog. "


def _build_prompt() -> str:
    import os
    try:
        n = int(os.environ.get("CLAUDE_MONITOR_PADDING_SENTENCES", "500"))
    except ValueError:
        n = 500
    n = max(10, min(n, 2000))
    padding = _PADDING_SENTENCE * n
    return (
        "Below is a block of filler text used for calibration purposes. "
        "Please read it, then follow the instruction at the end.\n\n"
        "<filler>\n"
        f"{padding}\n"
        "</filler>\n\n"
        "INSTRUCTION: Count from 1 to 300, each number on its own line. "
        "After all numbers, write exactly one line: CALIBRATION_COMPLETE\n"
        "Do NOT add any other text, explanation, or formatting."
    )


CALIBRATION_PROMPT = _build_prompt()

# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
CALIBRATION_DIR = Path.home() / ".config" / "claude-monitor"
CALIBRATION_FILE = CALIBRATION_DIR / "calibration.json"


@dataclass
class CalibrationRecord:
    timestamp: str
    measured_input: int
    measured_output: int
    measured_cache_create: int
    measured_cache_read: int
    measured_billable: int
    user_pct_before: float | None = None
    user_pct_after: float | None = None
    computed_limit: int | None = None
    window: str = "5h"  # which window this calibrates


@dataclass
class Observation:
    """One paired observation of Claude.ai UI % + our tool's billable tokens."""

    timestamp: str
    plan: str
    snapshot_5h_billable: int
    snapshot_weekly_billable: int
    snapshot_weekly_sonnet_billable: int
    ui_pct_5h: float | None = None
    ui_pct_weekly: float | None = None
    ui_pct_weekly_sonnet: float | None = None


@dataclass
class CalibrationData:
    records: list[CalibrationRecord] = field(default_factory=list)
    observations: list[Observation] = field(default_factory=list)
    calibrated_5h: int | None = None
    calibrated_weekly: int | None = None
    calibrated_weekly_sonnet: int | None = None


def _load_data() -> CalibrationData:
    if CALIBRATION_FILE.exists():
        raw = json.loads(CALIBRATION_FILE.read_text())
        recs = [CalibrationRecord(**r) for r in raw.get("records", [])]
        obs = [Observation(**o) for o in raw.get("observations", [])]
        return CalibrationData(
            records=recs,
            observations=obs,
            calibrated_5h=raw.get("calibrated_5h"),
            calibrated_weekly=raw.get("calibrated_weekly"),
            # Backward compat: older files used "calibrated_weekly_opus".
            calibrated_weekly_sonnet=raw.get(
                "calibrated_weekly_sonnet", raw.get("calibrated_weekly_opus")
            ),
        )
    return CalibrationData()


def _save_data(data: CalibrationData) -> None:
    CALIBRATION_DIR.mkdir(parents=True, exist_ok=True)
    out = {
        "records": [asdict(r) for r in data.records],
        "observations": [asdict(o) for o in data.observations],
        "calibrated_5h": data.calibrated_5h,
        "calibrated_weekly": data.calibrated_weekly,
        "calibrated_weekly_sonnet": data.calibrated_weekly_sonnet,
    }
    CALIBRATION_FILE.write_text(json.dumps(out, indent=2) + "\n")


def load_calibrated_limits() -> PlanLimits | None:
    """Return calibrated limits if they exist, else None."""
    data = _load_data()
    if data.calibrated_5h is None and data.calibrated_weekly is None and data.calibrated_weekly_sonnet is None:
        return None
    return PlanLimits(
        h5=data.calibrated_5h or 0,
        weekly_total=data.calibrated_weekly or 0,
        weekly_sonnet=data.calibrated_weekly_sonnet or 0,
    )


def _estimate_limit(billable_values: list[int], pct_values: list[float]) -> int | None:
    """Given N paired (billable, claude.ai pct) observations, estimate the real limit.

    Uses simple ratio averaging: limit ≈ mean(billable[i] / (pct[i]/100))
    over all points where pct > 0.5 (skip zeros because they carry no signal).
    """
    ratios = []
    for b, p in zip(billable_values, pct_values):
        if p is None or p < 0.5 or b <= 0:
            continue
        ratios.append(b / (p / 100.0))
    if not ratios:
        return None
    return int(sum(ratios) / len(ratios))


# ---------------------------------------------------------------------------
# Snapshot helpers
# ---------------------------------------------------------------------------
def _take_snapshot(settings: Settings) -> tuple[UsageStore, FileTailer, Snapshot]:
    store = UsageStore()
    tailer = FileTailer()
    seed_and_tail(store, tailer, settings.claude_dir)
    snap = store.snapshot(settings)
    return store, tailer, snap


def _fmt(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


# ---------------------------------------------------------------------------
# Main calibration flow
# ---------------------------------------------------------------------------
def run_calibrate(settings: Settings, skip_prompt: bool = False) -> None:
    """Send a calibration prompt, measure delta, ask user for % to compute limit."""
    console = Console()

    # Step 1: before snapshot
    console.print("\n[bold bright_green][ calibrate ] step 1/5: taking BEFORE snapshot...[/]")
    store, tailer, snap_before = _take_snapshot(settings)
    b5 = snap_before.window_5h.billable_tokens
    console.print(f"  5h billable before: [bold]{_fmt(b5)}[/] tokens\n")

    if not skip_prompt:
        # Step 2: send calibration prompt
        console.print("[bold bright_green][ calibrate ] step 2/5: sending calibration prompt via `claude -p` ...[/]")
        console.print("  (padding ~5k input tokens + requesting 300-number output)")
        console.print("  [green4](prompt piped via stdin to avoid Windows argv limits)[/]\n")

        # Resolve the executable — on Windows `claude` is often claude.cmd
        # and subprocess won't find it without shutil.which.
        import shutil
        claude_exe = shutil.which("claude")
        if claude_exe is None:
            console.print("[red]ERROR: `claude` CLI not found on PATH.[/]")
            console.print("Install it: [bold]npm install -g @anthropic-ai/claude-code[/]")
            return

        try:
            result = subprocess.run(
                [claude_exe, "-p"],
                input=CALIBRATION_PROMPT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=300,
                cwd=str(Path.home()),
            )
        except subprocess.TimeoutExpired:
            console.print("[red]ERROR: calibration prompt timed out after 300s.[/]")
            return
        except OSError as e:
            console.print(f"[red]ERROR spawning claude: {e}[/]")
            return

        if result.returncode != 0:
            console.print(f"[yellow]Warning: claude exited with code {result.returncode}[/]")
            if result.returncode in (3221226505, -1073740791):
                console.print(
                    "  [yellow]That's STATUS_STACK_BUFFER_OVERRUN — usually the claude CLI\n"
                    "  crashed on a long input. Try reducing the prompt size via\n"
                    "  CLAUDE_MONITOR_PADDING_SENTENCES env var (default 500, try 100).[/]"
                )
            if result.stderr:
                console.print(f"  stderr: {result.stderr[:500]}")
            if result.stdout:
                console.print(f"  stdout: {result.stdout[:500]}")

        # Check output
        output_lines = (result.stdout or "").strip().split("\n")
        has_marker = any("CALIBRATION_COMPLETE" in line for line in output_lines)
        console.print(f"\n  claude responded: {len(output_lines)} lines, "
                       f"marker={'[green]found[/]' if has_marker else '[yellow]not found[/]'}")

        # Wait for JSONL flush
        console.print("  waiting 3s for JSONL flush...")
        time.sleep(3)
    else:
        console.print("[bold bright_green][ calibrate ] step 2/5: skipped (--no-send)[/]")
        console.print("  Using whatever happened between snapshots.\n")

    # Step 3: after snapshot
    console.print("\n[bold bright_green][ calibrate ] step 3/5: taking AFTER snapshot...[/]")
    seed_and_tail(store, tailer, settings.claude_dir)
    snap_after = store.snapshot(settings)
    a5 = snap_after.window_5h.billable_tokens
    console.print(f"  5h billable after:  [bold]{_fmt(a5)}[/] tokens\n")

    # Step 4: compute delta
    delta = a5 - b5
    console.print("[bold bright_green][ calibrate ] step 4/5: measured delta[/]")

    # Find the new records
    delta_input = snap_after.window_5h.billable_tokens - snap_before.window_5h.billable_tokens
    delta_cache_r = snap_after.window_5h.cache_read_tokens - snap_before.window_5h.cache_read_tokens

    # Per-field deltas from the by_family breakdowns
    di = do = dc = dcr = 0
    for fam in set(list(snap_after.window_5h.by_family) + list(snap_before.window_5h.by_family)):
        after_m = snap_after.window_5h.by_family.get(fam)
        before_m = snap_before.window_5h.by_family.get(fam)
        if after_m:
            ai, ao, ac, ar = after_m.input, after_m.output, after_m.cache_create, after_m.cache_read
        else:
            ai = ao = ac = ar = 0
        if before_m:
            bi, bo, bc, br = before_m.input, before_m.output, before_m.cache_create, before_m.cache_read
        else:
            bi = bo = bc = br = 0
        di += ai - bi
        do += ao - bo
        dc += ac - bc
        dcr += ar - br

    tbl = Table(show_header=False, border_style="bright_green", padding=(0, 2))
    tbl.add_column(style="green4")
    tbl.add_column(justify="right", style="bright_green")
    tbl.add_row("input tokens", _fmt(di))
    tbl.add_row("output tokens", _fmt(do))
    tbl.add_row("cache_creation tokens", _fmt(dc))
    tbl.add_row("cache_read tokens (free)", _fmt(dcr))
    tbl.add_row("─" * 25, "─" * 10)
    tbl.add_row("[bold]billable delta[/]", f"[bold]{_fmt(delta)}[/]")
    console.print(tbl)

    if delta <= 0:
        console.print("\n[yellow]Delta is 0 or negative — no new usage detected.[/]")
        console.print("Make sure the calibration prompt actually went through.")
        return

    # Step 5: ask user for % change
    console.print("\n[bold bright_green][ calibrate ] step 5/5: compute your real limit[/]")
    console.print(
        "\n  Now check your Claude.ai rate limit indicator (the bar in Settings,\n"
        "  or the warning message if you're close to the limit).\n"
    )

    try:
        pct_before_str = console.input(
            "[bright_green]  % usage BEFORE this test (0-100, or Enter to skip): [/]"
        ).strip()
        pct_after_str = console.input(
            "[bright_green]  % usage AFTER  this test (0-100, or Enter to skip): [/]"
        ).strip()
    except (EOFError, KeyboardInterrupt):
        console.print("\n[yellow]Skipped. Delta saved without limit computation.[/]")
        pct_before_str = pct_after_str = ""

    pct_before = float(pct_before_str) if pct_before_str else None
    pct_after = float(pct_after_str) if pct_after_str else None

    computed_limit: int | None = None
    if pct_before is not None and pct_after is not None:
        pct_delta = pct_after - pct_before
        if pct_delta > 0:
            computed_limit = int(delta / (pct_delta / 100.0))
            console.print(
                f"\n  [bold bright_green]Computed 5h limit: "
                f"{computed_limit:,} tokens[/]"
            )
            console.print(
                f"  (you used {_fmt(delta)} tokens = {pct_delta:.1f}% → "
                f"100% ≈ {computed_limit:,})"
            )
        else:
            console.print("[yellow]  % didn't increase — can't compute limit.[/]")
    else:
        console.print(
            f"\n  Delta recorded: [bold]{_fmt(delta)}[/] billable tokens.\n"
            "  Run `claude-monitor calibrate` again with % values to compute limits,\n"
            "  or use `claude-monitor mark-limit` when you get rate-limited."
        )

    # Save
    record = CalibrationRecord(
        timestamp=datetime.now(timezone.utc).isoformat(),
        measured_input=di,
        measured_output=do,
        measured_cache_create=dc,
        measured_cache_read=dcr,
        measured_billable=delta,
        user_pct_before=pct_before,
        user_pct_after=pct_after,
        computed_limit=computed_limit,
    )
    data = _load_data()
    data.records.append(record)
    if computed_limit is not None:
        data.calibrated_5h = computed_limit
        console.print(
            f"\n  [bold green]✓ Saved calibrated 5h limit: {computed_limit:,}[/]"
        )
        console.print(
            "  Future runs will use this unless you pass --limit-5h or --plan."
        )
    _save_data(data)
    console.print(f"  Calibration data saved to: {CALIBRATION_FILE}\n")


# ---------------------------------------------------------------------------
# mark-limit: user hit the rate limit, record current window as the cap
# ---------------------------------------------------------------------------
def run_mark_limit(settings: Settings, window: str = "5h") -> None:
    """Record the current window total as the actual limit (user just got rate-limited)."""
    console = Console()
    _, _, snap = _take_snapshot(settings)

    data = _load_data()

    if window == "5h":
        used = snap.window_5h.billable_tokens
        data.calibrated_5h = used
        label = "5h"
    elif window == "weekly":
        used = snap.window_weekly.billable_tokens
        data.calibrated_weekly = used
        label = "weekly"
    elif window in ("weekly_sonnet", "weekly_opus"):
        # Backward compat: accept both names (we now call it weekly_sonnet).
        used = snap.window_weekly_sonnet.billable_tokens
        data.calibrated_weekly_sonnet = used
        label = "weekly_sonnet"
    else:
        console.print(f"[red]Unknown window: {window}[/]")
        return

    record = CalibrationRecord(
        timestamp=datetime.now(timezone.utc).isoformat(),
        measured_input=0,
        measured_output=0,
        measured_cache_create=0,
        measured_cache_read=0,
        measured_billable=used,
        window=label,
        computed_limit=used,
    )
    data.records.append(record)
    _save_data(data)

    console.print(
        f"\n  [bold bright_green]✓ Marked {label} limit: {used:,} tokens[/]"
    )
    console.print(f"  (based on your current {label} window usage at the moment you got rate-limited)")
    console.print(f"  Saved to: {CALIBRATION_FILE}\n")


# ---------------------------------------------------------------------------
# record: paired (snapshot, Claude.ai UI %) observation
# ---------------------------------------------------------------------------
def _prompt_pct(console: Console, label: str, preset: float | None) -> float | None:
    if preset is not None:
        return preset
    try:
        raw = console.input(
            f"[bright_green]  % for {label} (0-100, or Enter to skip): [/]"
        ).strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        console.print(f"  [yellow]couldn't parse {raw!r}, skipping[/]")
        return None


def run_record(
    settings: Settings,
    pct_5h: float | None = None,
    pct_weekly: float | None = None,
    pct_weekly_sonnet: float | None = None,
) -> None:
    """Take a snapshot + record Claude.ai UI percentages at the same moment."""
    console = Console()
    _, _, snap = _take_snapshot(settings)

    console.print("\n[bold bright_green]snapshot at this moment[/]\n")
    tbl = Table(border_style="bright_green", show_header=True)
    tbl.add_column("window", style="green4")
    tbl.add_column("billable", justify="right", style="bright_green")
    tbl.add_column("default limit", justify="right", style="green4")
    tbl.add_column("shown %", justify="right", style="green4")
    tbl.add_row(
        "5h",
        _fmt(snap.window_5h.billable_tokens),
        _fmt(snap.window_5h.limit),
        f"{snap.window_5h.pct_used:.1f}%",
    )
    tbl.add_row(
        "weekly",
        _fmt(snap.window_weekly.billable_tokens),
        _fmt(snap.window_weekly.limit),
        f"{snap.window_weekly.pct_used:.1f}%",
    )
    tbl.add_row(
        "weekly_sonnet",
        _fmt(snap.window_weekly_sonnet.billable_tokens),
        _fmt(snap.window_weekly_sonnet.limit),
        f"{snap.window_weekly_sonnet.pct_used:.1f}%",
    )
    console.print(tbl)

    any_preset = any(v is not None for v in (pct_5h, pct_weekly, pct_weekly_sonnet))
    if not any_preset:
        console.print(
            "\n  [green4]Now open Claude.ai → Settings → Usage and type the three % values shown.[/]"
        )

    p5 = _prompt_pct(console, "5-hour session", pct_5h)
    pw = _prompt_pct(console, "Weekly · All models", pct_weekly)
    ps = _prompt_pct(console, "Weekly · Sonnet only", pct_weekly_sonnet)

    obs = Observation(
        timestamp=datetime.now(timezone.utc).isoformat(),
        plan=str(settings.plan),
        snapshot_5h_billable=snap.window_5h.billable_tokens,
        snapshot_weekly_billable=snap.window_weekly.billable_tokens,
        snapshot_weekly_sonnet_billable=snap.window_weekly_sonnet.billable_tokens,
        ui_pct_5h=p5,
        ui_pct_weekly=pw,
        ui_pct_weekly_sonnet=ps,
    )

    data = _load_data()
    data.observations.append(obs)

    # Recompute calibrated limits from all observations.
    lim5 = _estimate_limit(
        [o.snapshot_5h_billable for o in data.observations],
        [o.ui_pct_5h for o in data.observations],
    )
    limw = _estimate_limit(
        [o.snapshot_weekly_billable for o in data.observations],
        [o.ui_pct_weekly for o in data.observations],
    )
    lims = _estimate_limit(
        [o.snapshot_weekly_sonnet_billable for o in data.observations],
        [o.ui_pct_weekly_sonnet for o in data.observations],
    )
    if lim5:
        data.calibrated_5h = lim5
    if limw:
        data.calibrated_weekly = limw
    if lims:
        data.calibrated_weekly_sonnet = lims

    _save_data(data)

    console.print("\n[bold bright_green]estimated real limits (from all observations):[/]\n")
    rtbl = Table(border_style="bright_green", show_header=True)
    rtbl.add_column("window", style="green4")
    rtbl.add_column("estimate", justify="right", style="bright_green")
    rtbl.add_column("n obs", justify="right", style="green4")

    def _count_useful(pct_getter) -> int:
        return sum(1 for o in data.observations if (pct_getter(o) or 0) >= 0.5)

    rtbl.add_row(
        "5h",
        f"{lim5:,}" if lim5 else "need more data (non-zero %)",
        str(_count_useful(lambda o: o.ui_pct_5h)),
    )
    rtbl.add_row(
        "weekly",
        f"{limw:,}" if limw else "need more data",
        str(_count_useful(lambda o: o.ui_pct_weekly)),
    )
    rtbl.add_row(
        "weekly_sonnet",
        f"{lims:,}" if lims else "need more data (0% gives no signal)",
        str(_count_useful(lambda o: o.ui_pct_weekly_sonnet)),
    )
    console.print(rtbl)
    console.print(
        f"\n  Saved observation → {CALIBRATION_FILE}"
        f"\n  [green4]Tip: record another observation when the UI % has moved a few points; "
        "the estimate gets sharper each time.[/]\n"
    )


# ---------------------------------------------------------------------------
# show calibration history
# ---------------------------------------------------------------------------
def show_calibration(settings: Settings) -> None:
    """Display calibration history and current calibrated limits."""
    console = Console()
    data = _load_data()

    console.print("\n[bold bright_green]calibration status[/]\n")

    # Current calibrated values
    tbl = Table(title="calibrated limits", border_style="bright_green", show_header=True)
    tbl.add_column("window", style="green4")
    tbl.add_column("calibrated", justify="right", style="bright_green")
    tbl.add_column("default", justify="right", style="green4")
    tbl.add_column("source", style="green4")

    def _src(v: int | None) -> str:
        return "calibrated" if v else "default"

    lim = settings.limits
    tbl.add_row(
        "5h",
        f"{data.calibrated_5h:,}" if data.calibrated_5h else "—",
        f"{lim.h5:,}",
        _src(data.calibrated_5h),
    )
    tbl.add_row(
        "weekly",
        f"{data.calibrated_weekly:,}" if data.calibrated_weekly else "—",
        f"{lim.weekly_total:,}",
        _src(data.calibrated_weekly),
    )
    tbl.add_row(
        "weekly_sonnet",
        f"{data.calibrated_weekly_sonnet:,}" if data.calibrated_weekly_sonnet else "—",
        f"{lim.weekly_sonnet:,}",
        _src(data.calibrated_weekly_sonnet),
    )
    console.print(tbl)

    # Observations table
    if data.observations:
        console.print(f"\n  [green4]{len(data.observations)} paired observation(s):[/]\n")
        otbl = Table(border_style="green4", show_header=True)
        otbl.add_column("time", style="green4")
        otbl.add_column("5h bill", justify="right")
        otbl.add_column("5h UI%", justify="right")
        otbl.add_column("wk bill", justify="right")
        otbl.add_column("wk UI%", justify="right")
        otbl.add_column("son bill", justify="right")
        otbl.add_column("son UI%", justify="right")
        for o in data.observations[-10:]:
            otbl.add_row(
                o.timestamp[:19],
                _fmt(o.snapshot_5h_billable),
                f"{o.ui_pct_5h:.1f}" if o.ui_pct_5h is not None else "—",
                _fmt(o.snapshot_weekly_billable),
                f"{o.ui_pct_weekly:.1f}" if o.ui_pct_weekly is not None else "—",
                _fmt(o.snapshot_weekly_sonnet_billable),
                f"{o.ui_pct_weekly_sonnet:.1f}" if o.ui_pct_weekly_sonnet is not None else "—",
            )
        console.print(otbl)

    # Records table (from calibrate / mark-limit)
    if data.records:
        console.print(f"\n  [green4]{len(data.records)} calibration record(s):[/]\n")
        htbl = Table(border_style="green4", show_header=True)
        htbl.add_column("time", style="green4")
        htbl.add_column("window", style="green4")
        htbl.add_column("billable", justify="right")
        htbl.add_column("% before", justify="right")
        htbl.add_column("% after", justify="right")
        htbl.add_column("computed limit", justify="right", style="bright_green")
        for r in data.records[-10:]:
            htbl.add_row(
                r.timestamp[:19],
                r.window,
                _fmt(r.measured_billable),
                f"{r.user_pct_before:.1f}" if r.user_pct_before is not None else "—",
                f"{r.user_pct_after:.1f}" if r.user_pct_after is not None else "—",
                f"{r.computed_limit:,}" if r.computed_limit else "—",
            )
        console.print(htbl)

    if not data.records and not data.observations:
        console.print("\n  No calibration data yet.")
        console.print("  Run [bold]claude-monitor record[/] to add a paired observation.")
        console.print("  Or [bold]claude-monitor calibrate[/] to send a test prompt.\n")
    else:
        console.print()
