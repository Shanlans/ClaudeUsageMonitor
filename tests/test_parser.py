from __future__ import annotations

import json
from pathlib import Path

from claude_monitor.parser import parse_line


def test_user_rows_return_none(tmp_path: Path) -> None:
    raw = json.dumps({
        "type": "user",
        "timestamp": "2026-04-09T12:00:00.000Z",
        "message": {"role": "user", "content": "hello"},
    })
    assert parse_line(raw, tmp_path / "x.jsonl") is None


def test_assistant_without_usage_returns_none(tmp_path: Path) -> None:
    raw = json.dumps({
        "type": "assistant",
        "timestamp": "2026-04-09T12:00:00.000Z",
        "message": {"role": "assistant", "model": "claude-sonnet-4", "content": "hi"},
    })
    assert parse_line(raw, tmp_path / "x.jsonl") is None


def test_malformed_json_returns_none(tmp_path: Path) -> None:
    assert parse_line("{not json", tmp_path / "x.jsonl") is None
    assert parse_line("", tmp_path / "x.jsonl") is None


def test_extracts_all_token_fields(tmp_path: Path) -> None:
    raw = json.dumps({
        "type": "assistant",
        "timestamp": "2026-04-09T12:00:00.000Z",
        "sessionId": "sess",
        "requestId": "req-1",
        "uuid": "u-1",
        "message": {
            "role": "assistant",
            "model": "claude-opus-4-20250514",
            "usage": {
                "input_tokens": 10,
                "output_tokens": 20,
                "cache_creation_input_tokens": 5,
                "cache_read_input_tokens": 100,
                "cache_creation": {
                    "ephemeral_5m_input_tokens": 3,
                    "ephemeral_1h_input_tokens": 2,
                },
                "service_tier": "standard",
            },
        },
    })
    rec = parse_line(raw, tmp_path / "x.jsonl")
    assert rec is not None
    assert rec.model == "claude-opus-4-20250514"
    assert rec.family == "opus"
    assert rec.input_tokens == 10
    assert rec.output_tokens == 20
    assert rec.cache_creation_tokens == 5
    assert rec.cache_read_tokens == 100
    assert rec.ephemeral_5m == 3
    assert rec.ephemeral_1h == 2
    assert rec.billable_tokens == 35  # 10 + 20 + 5, cache_read excluded
    assert rec.request_id == "req-1"


def test_tolerates_missing_cache_creation_subkey(tmp_path: Path) -> None:
    raw = json.dumps({
        "type": "assistant",
        "timestamp": "2026-04-09T12:00:00.000Z",
        "sessionId": "s",
        "uuid": "u",
        "message": {
            "role": "assistant",
            "model": "claude-haiku-4-5-20251001",
            "usage": {"input_tokens": 1, "output_tokens": 2},
        },
    })
    rec = parse_line(raw, tmp_path / "x.jsonl")
    assert rec is not None
    assert rec.family == "haiku"
    assert rec.cache_creation_tokens == 0
    assert rec.ephemeral_5m == 0
    assert rec.ephemeral_1h == 0
