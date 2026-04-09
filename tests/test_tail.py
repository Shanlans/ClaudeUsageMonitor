from __future__ import annotations

from pathlib import Path

from claude_monitor.tail import FileTailer


def test_tailer_yields_only_new_complete_lines(tmp_path: Path) -> None:
    f = tmp_path / "log.jsonl"
    f.write_text('{"a":1}\n{"a":2}\n')

    t = FileTailer()
    first = list(t.read_new(f))
    assert first == ['{"a":1}', '{"a":2}']

    # No new bytes → nothing yielded.
    assert list(t.read_new(f)) == []

    # Append a partial line without newline — should not yield yet.
    with f.open("a") as fh:
        fh.write('{"a":3')
    assert list(t.read_new(f)) == []

    # Complete the line — should yield it.
    with f.open("a") as fh:
        fh.write('}\n')
    assert list(t.read_new(f)) == ['{"a":3}']


def test_tailer_handles_missing_file(tmp_path: Path) -> None:
    t = FileTailer()
    assert list(t.read_new(tmp_path / "missing.jsonl")) == []


def test_tailer_handles_truncation(tmp_path: Path) -> None:
    f = tmp_path / "log.jsonl"
    f.write_text('{"a":1}\n{"a":2}\n')
    t = FileTailer()
    list(t.read_new(f))
    # Truncate and rewrite.
    f.write_text('{"a":9}\n')
    result = list(t.read_new(f))
    assert result == ['{"a":9}']
