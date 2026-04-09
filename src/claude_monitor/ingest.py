"""Shared ingestion helpers used by CLI, TUI, and web server."""

from __future__ import annotations

from pathlib import Path

from claude_monitor.aggregator import UsageStore
from claude_monitor.discovery import iter_transcripts
from claude_monitor.parser import parse_line
from claude_monitor.tail import FileTailer


def seed_and_tail(store: UsageStore, tailer: FileTailer, claude_dir: Path) -> int:
    """Read any new lines from every discovered transcript into the store.

    On first call this is a full seed. On subsequent calls it only reads
    bytes appended since last time.
    """
    added = 0
    for path in iter_transcripts(claude_dir):
        for line in tailer.read_new(path):
            rec = parse_line(line, path)
            if rec is None:
                continue
            if store.ingest(rec):
                added += 1
    store.prune()
    return added
