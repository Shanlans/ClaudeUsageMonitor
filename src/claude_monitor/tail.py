"""Incremental reader that tracks a byte offset per file."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path


class FileTailer:
    """Reads append-only files incrementally, yielding only complete lines.

    Maintains `(path → byte_offset)` state and a small per-path buffer for
    partial trailing lines that haven't been terminated with a newline yet.
    """

    def __init__(self) -> None:
        self._offsets: dict[Path, int] = {}
        self._partial: dict[Path, bytes] = {}

    def read_new(self, path: Path) -> Iterator[str]:
        """Yield any complete new lines appended since the last call."""
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            return
        start = self._offsets.get(path, 0)

        # Handle truncation / rotation defensively.
        if size < start:
            start = 0
            self._partial.pop(path, None)

        if size == start:
            return

        with path.open("rb") as fh:
            fh.seek(start)
            chunk = fh.read(size - start)

        self._offsets[path] = size
        buf = self._partial.pop(path, b"") + chunk

        # Split on newline; keep the trailing fragment for next call.
        lines = buf.split(b"\n")
        if not buf.endswith(b"\n"):
            self._partial[path] = lines[-1]
            lines = lines[:-1]
        else:
            lines = lines[:-1]  # drop empty trailing element

        for line in lines:
            if not line:
                continue
            try:
                yield line.decode("utf-8")
            except UnicodeDecodeError:
                continue

    def reset(self, path: Path | None = None) -> None:
        if path is None:
            self._offsets.clear()
            self._partial.clear()
        else:
            self._offsets.pop(path, None)
            self._partial.pop(path, None)
