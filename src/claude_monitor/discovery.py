"""Find all Claude Code transcript files under the projects directory."""

from __future__ import annotations

from pathlib import Path


def iter_transcripts(claude_dir: Path) -> list[Path]:
    """Return all *.jsonl files under claude_dir (recursive).

    Catches both top-level session files and subagent files, which live under
    `<session-uuid>/subagents/*.jsonl`.
    """
    if not claude_dir.exists():
        return []
    return sorted(claude_dir.rglob("*.jsonl"))
