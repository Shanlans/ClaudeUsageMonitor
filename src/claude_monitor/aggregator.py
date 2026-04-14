"""In-memory store that computes rolling windows, burn rate, and snapshots."""

from __future__ import annotations

import bisect
from datetime import datetime, timedelta, timezone

from claude_monitor.config import (
    BURN_WINDOW_SECONDS,
    RETENTION_GRACE_SECONDS,
    WINDOW_5H_SECONDS,
    WINDOW_WEEKLY_SECONDS,
    PlanLimits,
    Settings,
    TokenWeights,
)
from claude_monitor.models import ModelBreakdown, Snapshot, UsageRecord, Window
from claude_monitor.pricing import PriceRow, compute_cost


def _weighted_billable(rec: UsageRecord, w: TokenWeights) -> float:
    return (
        w.input * rec.input_tokens
        + w.output * rec.output_tokens
        + w.cache_create * rec.cache_creation_tokens
        + w.cache_read * rec.cache_read_tokens
    )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UsageStore:
    """Holds usage records and computes rolling windows on demand."""

    def __init__(self, price_table: list[PriceRow] | None = None) -> None:
        self._records: list[UsageRecord] = []
        self._timestamps: list[datetime] = []  # parallel to _records for bisect
        self._seen: set[str] = set()
        self._price_table = price_table
        self._total_cost = 0.0

    # -- ingestion -----------------------------------------------------------

    def ingest(self, record: UsageRecord) -> bool:
        """Add a record. Returns False if deduped."""
        key = record.dedup_key
        if key and key in self._seen:
            return False
        if key:
            self._seen.add(key)

        # Keep list sorted by timestamp (records usually arrive in order).
        idx = bisect.bisect_right(self._timestamps, record.timestamp)
        self._records.insert(idx, record)
        self._timestamps.insert(idx, record.timestamp)

        self._total_cost += compute_cost(
            model=record.model,
            input_tokens=record.input_tokens,
            output_tokens=record.output_tokens,
            cache_creation_tokens=record.cache_creation_tokens,
            cache_read_tokens=record.cache_read_tokens,
            table=self._price_table,
        )
        return True

    def prune(self, now: datetime | None = None) -> int:
        now = now or _utcnow()
        cutoff = now - timedelta(seconds=WINDOW_WEEKLY_SECONDS + RETENTION_GRACE_SECONDS)
        cut_idx = bisect.bisect_left(self._timestamps, cutoff)
        if cut_idx == 0:
            return 0
        del self._records[:cut_idx]
        del self._timestamps[:cut_idx]
        return cut_idx

    # -- queries -------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._records)

    @property
    def total_cost_usd(self) -> float:
        return self._total_cost

    def _slice(self, start: datetime, end: datetime) -> list[UsageRecord]:
        lo = bisect.bisect_left(self._timestamps, start)
        hi = bisect.bisect_right(self._timestamps, end)
        return self._records[lo:hi]

    def _build_window(
        self,
        *,
        label: str,
        start: datetime,
        end: datetime,
        limit: int,
        weights: TokenWeights,
        family_filter: str | None = None,
    ) -> Window:
        records = self._slice(start, end)
        by_family: dict[str, ModelBreakdown] = {}
        billable = 0.0
        cache_read = 0
        cost = 0.0
        count = 0
        for rec in records:
            if family_filter is not None and rec.family != family_filter:
                continue
            count += 1
            billable += _weighted_billable(rec, weights)
            cache_read += rec.cache_read_tokens
            mb = by_family.setdefault(rec.family, ModelBreakdown(family=rec.family))
            mb.records += 1
            mb.input += rec.input_tokens
            mb.output += rec.output_tokens
            mb.cache_create += rec.cache_creation_tokens
            mb.cache_read += rec.cache_read_tokens
            rc = compute_cost(
                model=rec.model,
                input_tokens=rec.input_tokens,
                output_tokens=rec.output_tokens,
                cache_creation_tokens=rec.cache_creation_tokens,
                cache_read_tokens=rec.cache_read_tokens,
                table=self._price_table,
            )
            mb.cost_usd += rc
            cost += rc

        return Window(
            label=label,
            start=start,
            end=end,
            billable_tokens=int(billable),
            cache_read_tokens=cache_read,
            limit=limit,
            records=count,
            by_family=by_family,
            cost_usd=cost,
        )

    def burn_rate(self, now: datetime, weights: TokenWeights) -> tuple[float, dict[str, float]]:
        start = now - timedelta(seconds=BURN_WINDOW_SECONDS)
        records = self._slice(start, now)
        minutes = BURN_WINDOW_SECONDS / 60.0
        total = 0.0
        by_family: dict[str, float] = {}
        for rec in records:
            w = _weighted_billable(rec, weights)
            total += w
            by_family[rec.family] = by_family.get(rec.family, 0.0) + w
        return (total / minutes, {k: v / minutes for k, v in by_family.items()})

    def _eta(self, window: Window, burn_per_min: float) -> timedelta | None:
        if burn_per_min <= 0 or window.billable_tokens >= window.limit or window.limit <= 0:
            return None
        remaining = window.limit - window.billable_tokens
        minutes = remaining / burn_per_min
        return timedelta(minutes=minutes)

    def snapshot(self, settings: Settings, now: datetime | None = None) -> Snapshot:
        now = now or _utcnow()
        limits: PlanLimits = settings.limits
        weights: TokenWeights = settings.weights

        w5h = self._build_window(
            label="5h",
            start=now - timedelta(seconds=WINDOW_5H_SECONDS),
            end=now,
            limit=limits.h5,
            weights=weights,
        )
        wweek = self._build_window(
            label="weekly",
            start=now - timedelta(seconds=WINDOW_WEEKLY_SECONDS),
            end=now,
            limit=limits.weekly_total,
            weights=weights,
        )
        wopus = self._build_window(
            label="weekly_opus",
            start=now - timedelta(seconds=WINDOW_WEEKLY_SECONDS),
            end=now,
            limit=limits.weekly_opus,
            weights=weights,
            family_filter="opus",
        )

        burn, burn_fam = self.burn_rate(now, weights)
        session_ids = sorted({r.session_id for r in self._slice(now - timedelta(seconds=WINDOW_5H_SECONDS), now) if r.session_id})

        return Snapshot(
            now=now,
            plan=settings.plan,
            limits=limits,
            window_5h=w5h,
            window_weekly=wweek,
            window_weekly_opus=wopus,
            burn_tokens_per_min=burn,
            burn_by_family=burn_fam,
            eta_5h=self._eta(w5h, burn),
            eta_weekly=self._eta(wweek, burn),
            eta_weekly_opus=self._eta(wopus, burn_fam.get("opus", 0.0)),
            active_session_ids=session_ids,
            total_cost_usd=self._total_cost,
        )
