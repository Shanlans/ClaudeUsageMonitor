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
        "calibrated_weekly_opus": 150000,
    }))
    with patch("claude_monitor.calibrate.CALIBRATION_FILE", f):
        lim = load_calibrated_limits()
    assert lim is not None
    assert lim.h5 == 75000
    assert lim.weekly_total == 2000000
    assert lim.weekly_opus == 150000


def test_computed_limit_math() -> None:
    """Verify the limit = delta / (pct_delta / 100) formula."""
    delta = 7000
    pct_before = 30.0
    pct_after = 38.5
    pct_delta = pct_after - pct_before  # 8.5
    computed = int(delta / (pct_delta / 100.0))
    assert computed == 82352  # 7000 / 0.085 = 82352.94...
