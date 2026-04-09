"""Per-model pricing used to estimate cost from raw token counts."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

_WARNED_UNKNOWN: set[str] = set()


@dataclass(frozen=True)
class PriceRow:
    prefix: str
    input: float
    output: float
    cache_create: float
    cache_read: float


# Approximate, user-overridable per-1M-token rates (USD). Ordered longest-prefix first.
# Override via --prices PATH or CLAUDE_MONITOR_PRICES_JSON env var.
DEFAULT_PRICE_TABLE: list[PriceRow] = [
    PriceRow("claude-3-5-sonnet", 3.00, 15.00, 3.75, 0.30),
    PriceRow("claude-3-5-haiku", 0.80, 4.00, 1.00, 0.08),
    PriceRow("claude-3-opus", 15.00, 75.00, 18.75, 1.50),
    PriceRow("claude-opus-4", 15.00, 75.00, 18.75, 1.50),
    PriceRow("claude-sonnet-4", 3.00, 15.00, 3.75, 0.30),
    PriceRow("claude-haiku-4", 1.00, 5.00, 1.25, 0.10),
]

_UNKNOWN = PriceRow("_unknown", 0.0, 0.0, 0.0, 0.0)


def load_price_table(path: Path | None = None) -> list[PriceRow]:
    """Load price table from JSON file (falls back to env var, then defaults)."""
    target = path
    if target is None:
        env = os.environ.get("CLAUDE_MONITOR_PRICES_JSON")
        if env:
            target = Path(env)
    if target is None or not target.exists():
        return list(DEFAULT_PRICE_TABLE)
    raw = json.loads(target.read_text())
    rows = [PriceRow(**row) for row in raw]
    # Sort longest-prefix first so match_model prefers specific entries.
    rows.sort(key=lambda r: len(r.prefix), reverse=True)
    return rows


def match_model(model: str, table: list[PriceRow] | None = None) -> PriceRow:
    rows = table if table is not None else DEFAULT_PRICE_TABLE
    # Longest-prefix match.
    best: PriceRow | None = None
    for row in rows:
        if model.startswith(row.prefix) and (best is None or len(row.prefix) > len(best.prefix)):
            best = row
    if best is None:
        if model not in _WARNED_UNKNOWN:
            log.warning("no price row for model %r; cost will be 0", model)
            _WARNED_UNKNOWN.add(model)
        return _UNKNOWN
    return best


def family_of(model: str) -> str:
    m = model.lower()
    if "opus" in m:
        return "opus"
    if "sonnet" in m:
        return "sonnet"
    if "haiku" in m:
        return "haiku"
    return "other"


def compute_cost(
    *,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_creation_tokens: int,
    cache_read_tokens: int,
    table: list[PriceRow] | None = None,
) -> float:
    row = match_model(model, table)
    return (
        input_tokens * row.input
        + output_tokens * row.output
        + cache_creation_tokens * row.cache_create
        + cache_read_tokens * row.cache_read
    ) / 1_000_000
