"""Plan limits, window sizes, and runtime settings."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

WINDOW_5H_SECONDS = 5 * 3600
WINDOW_WEEKLY_SECONDS = 7 * 24 * 3600
BURN_WINDOW_SECONDS = 600
POLL_INTERVAL = 2.0
RETENTION_GRACE_SECONDS = 600  # 10 minutes beyond weekly window


class Plan(StrEnum):
    pro = "pro"
    max5 = "max5"
    max20 = "max20"
    custom = "custom"


@dataclass(frozen=True)
class PlanLimits:
    """Billable-token caps for the three rolling windows."""

    h5: int
    weekly_total: int
    weekly_opus: int


@dataclass(frozen=True)
class TokenWeights:
    """Weights applied to each token class when computing billable usage.

    Default: only input + output count. Cache tokens are shown but not
    counted toward the rate-limit windows (Anthropic's actual counting
    formula is not publicly documented; this is the most defensible
    approximation for most users).

    Override via CLI flags or JSON — the right values for YOU are best
    discovered through calibration.
    """

    input: float = 1.0
    output: float = 1.0
    cache_create: float = 0.0
    cache_read: float = 0.0


def default_weights() -> TokenWeights:
    """Return the default weights. Kept as a function for easy monkey-patching in tests."""
    return TokenWeights()


# Approximate, user-overridable. Anthropic does not publish exact numbers
# and they shift over time. Override via CLI flags or CLAUDE_MONITOR_LIMITS_JSON.
PLAN_LIMITS: dict[Plan, PlanLimits] = {
    Plan.pro: PlanLimits(h5=19_000, weekly_total=300_000, weekly_opus=0),
    Plan.max5: PlanLimits(h5=88_000, weekly_total=1_900_000, weekly_opus=200_000),
    Plan.max20: PlanLimits(h5=220_000, weekly_total=7_600_000, weekly_opus=800_000),
}


def _default_claude_dir() -> Path:
    return Path(os.environ.get("CLAUDE_MONITOR_DIR", Path.home() / ".claude" / "projects"))


@dataclass
class Settings:
    plan: Plan = Plan.max5
    limits: PlanLimits = field(default_factory=lambda: PLAN_LIMITS[Plan.max5])
    weights: TokenWeights = field(default_factory=default_weights)
    claude_dir: Path = field(default_factory=_default_claude_dir)
    prices_path: Path | None = None


def load_limits(
    plan: Plan,
    *,
    h5: int | None = None,
    weekly: int | None = None,
    weekly_opus: int | None = None,
    limits_path: Path | None = None,
) -> PlanLimits:
    """Resolve effective plan limits with CLI/env/file overrides.

    Precedence: explicit h5/weekly/weekly_opus flags > --limits file > env var > plan default.
    For plan=custom, at least one explicit value is required.
    """
    base = PLAN_LIMITS.get(plan, PLAN_LIMITS[Plan.max5])

    # Env override
    env_path = os.environ.get("CLAUDE_MONITOR_LIMITS_JSON")
    file_override: dict = {}
    path_to_read = limits_path or (Path(env_path) if env_path else None)
    if path_to_read is not None and path_to_read.exists():
        file_override = json.loads(path_to_read.read_text())

    eff_h5 = h5 if h5 is not None else file_override.get("h5", base.h5)
    eff_weekly = weekly if weekly is not None else file_override.get("weekly_total", base.weekly_total)
    eff_weekly_opus = (
        weekly_opus if weekly_opus is not None else file_override.get("weekly_opus", base.weekly_opus)
    )

    if plan == Plan.custom and h5 is None and weekly is None and weekly_opus is None and not file_override:
        raise ValueError(
            "plan=custom requires at least one of --limit-5h / --limit-weekly / "
            "--limit-weekly-opus or a --limits JSON file"
        )

    return PlanLimits(h5=int(eff_h5), weekly_total=int(eff_weekly), weekly_opus=int(eff_weekly_opus))
