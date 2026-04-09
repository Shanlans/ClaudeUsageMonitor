from __future__ import annotations

from claude_monitor.pricing import compute_cost, family_of, match_model


def test_match_model_longest_prefix() -> None:
    row = match_model("claude-opus-4-20250514")
    assert row.prefix == "claude-opus-4"
    assert row.input == 15.00
    assert row.output == 75.00


def test_match_model_sonnet4() -> None:
    row = match_model("claude-sonnet-4-20250514")
    assert row.prefix == "claude-sonnet-4"
    assert row.output == 15.00


def test_match_model_haiku4() -> None:
    row = match_model("claude-haiku-4-5-20251001")
    assert row.prefix == "claude-haiku-4"


def test_match_model_unknown_returns_unknown_row() -> None:
    row = match_model("claude-exotic-99")
    assert row.prefix == "_unknown"
    assert row.input == 0


def test_family_of() -> None:
    assert family_of("claude-opus-4-20250514") == "opus"
    assert family_of("claude-sonnet-4-20250514") == "sonnet"
    assert family_of("claude-3-5-sonnet-20241022") == "sonnet"
    assert family_of("claude-haiku-4-5-20251001") == "haiku"
    assert family_of("something-else") == "other"


def test_compute_cost_math() -> None:
    # 1M input tokens of opus-4 = $15.00 exactly
    cost = compute_cost(
        model="claude-opus-4-20250514",
        input_tokens=1_000_000,
        output_tokens=0,
        cache_creation_tokens=0,
        cache_read_tokens=0,
    )
    assert cost == 15.00

    # 1M output tokens of opus-4 = $75.00
    cost = compute_cost(
        model="claude-opus-4-20250514",
        input_tokens=0,
        output_tokens=1_000_000,
        cache_creation_tokens=0,
        cache_read_tokens=0,
    )
    assert cost == 75.00

    # sonnet: 500k input + 500k output → 1.50 + 7.50 = 9.00
    cost = compute_cost(
        model="claude-sonnet-4-20250514",
        input_tokens=500_000,
        output_tokens=500_000,
        cache_creation_tokens=0,
        cache_read_tokens=0,
    )
    assert abs(cost - 9.00) < 1e-9


def test_compute_cost_unknown_model_is_zero() -> None:
    cost = compute_cost(
        model="claude-mystery-99",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_creation_tokens=0,
        cache_read_tokens=0,
    )
    assert cost == 0.0
