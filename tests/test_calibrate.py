from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from claude_monitor.calibrate import (
    CalibrationData,
    CalibrationRecord,
    _load_data,
    _save_data,
    load_calibrated_limits,
)


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    f = tmp_path / "calibration.json"
    rec = CalibrationRecord(
        timestamp="2026-04-09T12:00:00Z",
        measured_input=5000,
        measured_output=1200,
        measured_cache_create=800,
        measured_cache_read=30000,
        measured_billable=7000,
        user_pct_before=30.0,
        user_pct_after=38.5,
        computed_limit=82352,
        window="5h",
    )
    data = CalibrationData(records=[rec], calibrated_5h=82352)

    with patch("claude_monitor.calibrate.CALIBRATION_FILE", f), \
         patch("claude_monitor.calibrate.CALIBRATION_DIR", tmp_path):
        _save_data(data)
        loaded = _load_data()

    assert len(loaded.records) == 1
    assert loaded.records[0].measured_billable == 7000
    assert loaded.records[0].computed_limit == 82352
    assert loaded.calibrated_5h == 82352
    assert loaded.calibrated_weekly is None


def test_load_calibrated_limits_none_when_no_file(tmp_path: Path) -> None:
    with patch("claude_monitor.calibrate.CALIBRATION_FILE", tmp_path / "nope.json"):
        assert load_calibrated_limits() is None


def test_load_calibrated_limits_returns_plan_limits(tmp_path: Path) -> None:
    f = tmp_path / "calibration.json"
    f.write_text(json.dumps({
        "records": [],
        "calibrated_5h": 75000,
        "calibrated_weekly": 2000000,
        "calibrated_weekly_sonnet": 150000,
    }))
    with patch("claude_monitor.calibrate.CALIBRATION_FILE", f):
        lim = load_calibrated_limits()
    assert lim is not None
    assert lim.h5 == 75000
    assert lim.weekly_total == 2000000
    assert lim.weekly_sonnet == 150000


def test_backward_compat_weekly_opus_key(tmp_path: Path) -> None:
    """Older calibration.json files used `calibrated_weekly_opus`; must still load."""
    f = tmp_path / "calibration.json"
    f.write_text(json.dumps({
        "records": [],
        "calibrated_5h": 100000,
        "calibrated_weekly": 2000000,
        "calibrated_weekly_opus": 300000,  # old key
    }))
    with patch("claude_monitor.calibrate.CALIBRATION_FILE", f):
        lim = load_calibrated_limits()
    assert lim is not None
    assert lim.weekly_sonnet == 300000


def test_computed_limit_math() -> None:
    """Verify the limit = delta / (pct_delta / 100) formula."""
    delta = 7000
    pct_before = 30.0
    pct_after = 38.5
    pct_delta = pct_after - pct_before  # 8.5
    computed = int(delta / (pct_delta / 100.0))
    assert computed == 82352  # 7000 / 0.085 = 82352.94...


def test_estimate_limit_from_observations() -> None:
    """_estimate_limit averages the billable/pct ratio across observations."""
    from claude_monitor.calibrate import _estimate_limit

    # Three observations: (billable, claude.ai pct)
    billable = [18_300, 50_000, 120_000]
    pcts = [7.0, 20.0, 50.0]

    # ratios: 18300/0.07=261428, 50000/0.20=250000, 120000/0.50=240000
    # average: 250476
    result = _estimate_limit(billable, pcts)
    assert result is not None
    assert 249_000 < result < 252_000


def test_estimate_limit_skips_zero_pct() -> None:
    """Observations with pct<0.5 carry no signal and should be skipped."""
    from claude_monitor.calibrate import _estimate_limit

    result = _estimate_limit([10_000, 50_000], [0.0, 20.0])
    # Only the second observation is useful → 50000/0.2 = 250000
    assert result == 250_000


def test_estimate_limit_returns_none_when_no_useful_data() -> None:
    from claude_monitor.calibrate import _estimate_limit

    assert _estimate_limit([10_000], [0.0]) is None
    assert _estimate_limit([], []) is None
    assert _estimate_limit([100], [None]) is None


def test_load_limits_uses_calibration(tmp_path: Path) -> None:
    """Calibrated values should override plan defaults when present."""
    from claude_monitor.config import Plan, load_limits

    f = tmp_path / "calibration.json"
    f.write_text(json.dumps({
        "records": [],
        "observations": [],
        "calibrated_5h": 150_000,
        "calibrated_weekly": 3_000_000,
        "calibrated_weekly_sonnet": None,
    }))

    with patch("claude_monitor.calibrate.CALIBRATION_FILE", f):
        lim = load_limits(Plan.max5)

    # Calibrated values win for 5h and weekly.
    assert lim.h5 == 150_000
    assert lim.weekly_total == 3_000_000
    # weekly_sonnet had no calibration → plan default.
    assert lim.weekly_sonnet == 200_000


def test_load_limits_ignore_calibration_flag(tmp_path: Path) -> None:
    """--ignore-calibration should fall back to plan defaults."""
    from claude_monitor.config import PLAN_LIMITS, Plan, load_limits

    f = tmp_path / "calibration.json"
    f.write_text(json.dumps({
        "records": [],
        "observations": [],
        "calibrated_5h": 150_000,
        "calibrated_weekly": 3_000_000,
    }))

    with patch("claude_monitor.calibrate.CALIBRATION_FILE", f):
        lim = load_limits(Plan.max5, ignore_calibration=True)

    assert lim.h5 == PLAN_LIMITS[Plan.max5].h5
    assert lim.weekly_total == PLAN_LIMITS[Plan.max5].weekly_total


def test_load_limits_explicit_flag_beats_calibration(tmp_path: Path) -> None:
    """Explicit --limit-5h should override both calibration and defaults."""
    from claude_monitor.config import Plan, load_limits

    f = tmp_path / "calibration.json"
    f.write_text(json.dumps({
        "records": [],
        "observations": [],
        "calibrated_5h": 150_000,
    }))

    with patch("claude_monitor.calibrate.CALIBRATION_FILE", f):
        lim = load_limits(Plan.max5, h5=999_000)

    assert lim.h5 == 999_000
