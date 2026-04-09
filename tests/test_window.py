from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from claude_monitor.aggregator import UsageStore
from claude_monitor.config import PLAN_LIMITS, Plan, Settings
from claude_monitor.parser import parse_line


def _load(store: UsageStore, path: Path) -> int:
    n = 0
    for line in path.read_text().splitlines():
        rec = parse_line(line, path)
        if rec and store.ingest(rec):
            n += 1
    return n


def test_5h_window_excludes_old_and_dedups(sample_jsonl: Path, frozen_now: datetime) -> None:
    store = UsageStore()
    loaded = _load(store, sample_jsonl)
    # rows 2, 3, 5, 6 — user(#1) skipped, dup(#4) deduped, #7 loaded but >7d old,
    # but ingest returns True for #7 here (only snapshot filters by window).
    assert loaded == 5

    settings = Settings(plan=Plan.max5, limits=PLAN_LIMITS[Plan.max5])
    snap = store.snapshot(settings, now=frozen_now)

    # 5h window: rows 2 (100+200+50=350) + 3 (10+500=510) + 5 (40+60+20=120) = 980
    assert snap.window_5h.billable_tokens == 980
    assert snap.window_5h.records == 3
    # cache_read is tracked separately and NOT counted toward billable.
    assert snap.window_5h.cache_read_tokens == 1000
    # Dedup verified: row #4 (same requestId as #2) didn't add again.


def test_weekly_window_includes_more(sample_jsonl: Path, frozen_now: datetime) -> None:
    store = UsageStore()
    _load(store, sample_jsonl)
    settings = Settings(plan=Plan.max5, limits=PLAN_LIMITS[Plan.max5])
    snap = store.snapshot(settings, now=frozen_now)

    # Weekly adds row #6 (1000 + 2000 = 3000). Row #7 is >7d old → excluded.
    assert snap.window_weekly.billable_tokens == 980 + 3000
    assert snap.window_weekly.records == 4


def test_weekly_opus_window_only_counts_opus(sample_jsonl: Path, frozen_now: datetime) -> None:
    store = UsageStore()
    _load(store, sample_jsonl)
    settings = Settings(plan=Plan.max5, limits=PLAN_LIMITS[Plan.max5])
    snap = store.snapshot(settings, now=frozen_now)

    # Opus rows within 7d: row #3 (10+500=510) + row #6 (3000) = 3510
    assert snap.window_weekly_opus.billable_tokens == 510 + 3000
    assert snap.window_weekly_opus.records == 2
    assert "sonnet" not in snap.window_weekly_opus.by_family
    assert "haiku" not in snap.window_weekly_opus.by_family


def test_prune_drops_records_older_than_retention(sample_jsonl: Path, frozen_now: datetime) -> None:
    store = UsageStore()
    _load(store, sample_jsonl)
    before = len(store)
    dropped = store.prune(now=frozen_now)
    after = len(store)
    # Row #7 (8 days old) should be pruned.
    assert dropped == 1
    assert after == before - 1


def test_burn_rate_and_eta(sample_jsonl: Path, frozen_now: datetime) -> None:
    store = UsageStore()
    _load(store, sample_jsonl)
    settings = Settings(plan=Plan.max5, limits=PLAN_LIMITS[Plan.max5])

    # Shift "now" so only the 30-min-old haiku row is within the 10-min burn window.
    # Move now forward so that #5 is inside the 10min burn window: #5 is at now-30m,
    # so we set now = #5.timestamp + 5min → burn window covers it.
    shifted_now = frozen_now - timedelta(minutes=30) + timedelta(minutes=5)
    snap = store.snapshot(settings, now=shifted_now)

    # Row #5 billable = 40+60+20 = 120 in 10 minutes = 12 tok/min.
    assert snap.burn_tokens_per_min == 12.0
    # ETA to 5h limit (88,000) based on 12 tok/min should be a positive timedelta.
    assert snap.eta_5h is not None
    assert snap.eta_5h > timedelta(0)


def test_custom_plan_limits_override() -> None:
    from claude_monitor.config import load_limits

    lim = load_limits(Plan.custom, h5=50_000, weekly=800_000, weekly_opus=80_000)
    assert lim.h5 == 50_000
    assert lim.weekly_total == 800_000
    assert lim.weekly_opus == 80_000
