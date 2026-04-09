"""Parse a single JSONL transcript line into a UsageRecord."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from claude_monitor.models import UsageRecord
from claude_monitor.pricing import family_of


def _parse_timestamp(raw: str) -> datetime:
    # Claude Code writes ISO-8601 with trailing Z.
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    return datetime.fromisoformat(raw)


def parse_line(raw: str, source_path: Path) -> UsageRecord | None:
    """Return a UsageRecord if the line is an assistant message with usage data.

    Returns None for user rows, system rows, malformed JSON, or rows with no usage.
    """
    raw = raw.strip()
    if not raw:
        return None
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return None

    if obj.get("type") != "assistant":
        return None

    message = obj.get("message") or {}
    usage = message.get("usage")
    if not usage:
        return None

    model = message.get("model") or "unknown"
    # Skip synthetic rows — Claude Code emits these for internal bookkeeping;
    # they carry a fake "<synthetic>" model with no real billable tokens.
    if model == "<synthetic>":
        return None
    cache_creation = usage.get("cache_creation") or {}

    ts_raw = obj.get("timestamp")
    if not ts_raw:
        return None
    try:
        ts = _parse_timestamp(ts_raw)
    except ValueError:
        return None

    return UsageRecord(
        timestamp=ts,
        session_id=obj.get("sessionId", ""),
        request_id=obj.get("requestId"),
        uuid=obj.get("uuid", ""),
        model=model,
        family=family_of(model),
        input_tokens=int(usage.get("input_tokens", 0) or 0),
        output_tokens=int(usage.get("output_tokens", 0) or 0),
        cache_creation_tokens=int(usage.get("cache_creation_input_tokens", 0) or 0),
        cache_read_tokens=int(usage.get("cache_read_input_tokens", 0) or 0),
        ephemeral_5m=int(cache_creation.get("ephemeral_5m_input_tokens", 0) or 0),
        ephemeral_1h=int(cache_creation.get("ephemeral_1h_input_tokens", 0) or 0),
        service_tier=usage.get("service_tier"),
        source_path=source_path,
    )
