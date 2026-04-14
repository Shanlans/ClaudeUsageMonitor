"""Data classes for usage records, windows, and snapshots."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from claude_monitor.config import Plan, PlanLimits


@dataclass
class UsageRecord:
    timestamp: datetime
    session_id: str
    request_id: str | None
    uuid: str
    model: str
    family: str  # "opus" | "sonnet" | "haiku" | "other"
    input_tokens: int
    output_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    ephemeral_5m: int
    ephemeral_1h: int
    service_tier: str | None
    source_path: Path

    @property
    def billable_tokens(self) -> int:
        return self.input_tokens + self.output_tokens + self.cache_creation_tokens

    @property
    def dedup_key(self) -> str:
        return self.request_id or self.uuid


@dataclass
class ModelBreakdown:
    family: str
    records: int = 0
    input: int = 0
    output: int = 0
    cache_create: int = 0
    cache_read: int = 0
    cost_usd: float = 0.0

    @property
    def billable(self) -> int:
        return self.input + self.output + self.cache_create


@dataclass
class Window:
    label: str  # "5h" | "weekly" | "weekly_sonnet"
    start: datetime
    end: datetime
    billable_tokens: int
    cache_read_tokens: int
    limit: int
    records: int
    by_family: dict[str, ModelBreakdown]
    cost_usd: float

    @property
    def pct_used(self) -> float:
        if self.limit <= 0:
            return 0.0
        return 100.0 * self.billable_tokens / self.limit


@dataclass
class Snapshot:
    now: datetime
    plan: Plan
    limits: PlanLimits
    window_5h: Window
    window_weekly: Window
    window_weekly_sonnet: Window
    burn_tokens_per_min: float
    burn_by_family: dict[str, float]
    eta_5h: timedelta | None
    eta_weekly: timedelta | None
    eta_weekly_sonnet: timedelta | None
    active_session_ids: list[str] = field(default_factory=list)
    total_cost_usd: float = 0.0
