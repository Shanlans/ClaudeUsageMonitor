"""Shared fixtures — JSONL rows generated relative to a frozen `now`."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


FROZEN_NOW = datetime(2026, 4, 9, 12, 0, 0, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    # Match Claude Code's format: ISO-8601 with trailing Z.
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _assistant(
    *,
    ts: datetime,
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_creation_input_tokens: int = 0,
    cache_read_input_tokens: int = 0,
    ephemeral_5m: int = 0,
    request_id: str | None = None,
    uuid: str = "",
    session_id: str = "sess-1",
) -> str:
    usage = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_creation_input_tokens": cache_creation_input_tokens,
        "cache_read_input_tokens": cache_read_input_tokens,
        "service_tier": "standard",
    }
    if ephemeral_5m:
        usage["cache_creation"] = {
            "ephemeral_5m_input_tokens": ephemeral_5m,
            "ephemeral_1h_input_tokens": 0,
        }
    row = {
        "type": "assistant",
        "timestamp": _iso(ts),
        "sessionId": session_id,
        "requestId": request_id,
        "uuid": uuid or f"u-{ts.isoformat()}",
        "message": {
            "role": "assistant",
            "model": model,
            "usage": usage,
        },
    }
    return json.dumps(row)


@pytest.fixture
def frozen_now() -> datetime:
    return FROZEN_NOW


@pytest.fixture
def sample_jsonl(tmp_path: Path) -> Path:
    """Write a 7-line fixture covering the assertions from the plan."""
    lines: list[str] = []

    # 1. user message — must be ignored by parser.
    lines.append(json.dumps({
        "type": "user",
        "timestamp": _iso(FROZEN_NOW - timedelta(hours=1, minutes=10)),
        "sessionId": "sess-1",
        "uuid": "u-user",
        "message": {"role": "user", "content": "hi"},
    }))

    # 2. sonnet assistant, 1h ago — inside 5h.
    lines.append(_assistant(
        ts=FROZEN_NOW - timedelta(hours=1),
        model="claude-sonnet-4-20250514",
        input_tokens=100,
        output_tokens=200,
        cache_creation_input_tokens=50,
        cache_read_input_tokens=1000,
        request_id="req-sonnet-1",
        uuid="u-2",
    ))

    # 3. opus assistant, 2h ago — inside 5h.
    lines.append(_assistant(
        ts=FROZEN_NOW - timedelta(hours=2),
        model="claude-opus-4-20250514",
        input_tokens=10,
        output_tokens=500,
        request_id="req-opus-1",
        uuid="u-3",
    ))

    # 4. duplicate of row #2 (same requestId) — must dedup.
    lines.append(_assistant(
        ts=FROZEN_NOW - timedelta(hours=1),
        model="claude-sonnet-4-20250514",
        input_tokens=100,
        output_tokens=200,
        cache_creation_input_tokens=50,
        cache_read_input_tokens=1000,
        request_id="req-sonnet-1",
        uuid="u-4",
    ))

    # 5. haiku assistant, 30m ago with ephemeral cache.
    lines.append(_assistant(
        ts=FROZEN_NOW - timedelta(minutes=30),
        model="claude-haiku-4-5-20251001",
        input_tokens=40,
        output_tokens=60,
        cache_creation_input_tokens=20,
        ephemeral_5m=30,
        request_id="req-haiku-1",
        uuid="u-5",
    ))

    # 6. opus 1 day ago — outside 5h, inside weekly AND weekly_opus.
    lines.append(_assistant(
        ts=FROZEN_NOW - timedelta(days=1),
        model="claude-opus-4-20250514",
        input_tokens=1000,
        output_tokens=2000,
        request_id="req-opus-2",
        uuid="u-6",
    ))

    # 7. sonnet 8 days ago — outside ALL windows.
    lines.append(_assistant(
        ts=FROZEN_NOW - timedelta(days=8),
        model="claude-sonnet-4-20250514",
        input_tokens=500,
        output_tokens=500,
        request_id="req-old",
        uuid="u-7",
    ))

    path = tmp_path / "projects" / "-home-user-test" / "session-abc.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
    return path
