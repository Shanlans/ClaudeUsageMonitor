"""FastAPI app exposing JSON snapshot + SSE stream + a tiny static dashboard."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from claude_monitor.aggregator import UsageStore
from claude_monitor.config import POLL_INTERVAL, Settings
from claude_monitor.ingest import seed_and_tail
from claude_monitor.models import Snapshot
from claude_monitor.tail import FileTailer

STATIC_DIR = Path(__file__).parent / "static"


def _json_default(obj):
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, timedelta):
        return obj.total_seconds()
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, Path):
        return str(obj)
    if is_dataclass(obj):
        return asdict(obj)
    raise TypeError(f"Cannot serialize {type(obj).__name__}")


def snapshot_to_dict(snap: Snapshot) -> dict:
    return json.loads(json.dumps(asdict(snap), default=_json_default))


def build_app(settings: Settings) -> FastAPI:
    app = FastAPI(title="claude-monitor", version="0.1.0")
    store = UsageStore()
    tailer = FileTailer()
    update_event = asyncio.Event()
    poll_task: asyncio.Task | None = None

    async def _poll_loop() -> None:
        while True:
            try:
                added = await asyncio.to_thread(seed_and_tail, store, tailer, settings.claude_dir)
                if added:
                    update_event.set()
            except Exception:
                # Never crash the poll loop.
                pass
            await asyncio.sleep(POLL_INTERVAL)

    @app.on_event("startup")
    async def _startup() -> None:
        nonlocal poll_task
        # Full initial seed (sync, on the event loop worker).
        await asyncio.to_thread(seed_and_tail, store, tailer, settings.claude_dir)
        update_event.set()
        poll_task = asyncio.create_task(_poll_loop())

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        if poll_task is not None:
            poll_task.cancel()

    @app.get("/api/snapshot")
    async def api_snapshot() -> JSONResponse:
        snap = store.snapshot(settings)
        return JSONResponse(snapshot_to_dict(snap))

    @app.get("/api/stream")
    async def api_stream() -> StreamingResponse:
        async def gen():
            # Emit an immediate snapshot so clients don't see an empty page.
            snap = store.snapshot(settings)
            yield f"data: {json.dumps(snapshot_to_dict(snap))}\n\n"
            last_heartbeat = asyncio.get_event_loop().time()
            while True:
                try:
                    await asyncio.wait_for(update_event.wait(), timeout=1.0)
                    update_event.clear()
                    snap = store.snapshot(settings)
                    yield f"data: {json.dumps(snapshot_to_dict(snap))}\n\n"
                    last_heartbeat = asyncio.get_event_loop().time()
                except asyncio.TimeoutError:
                    # Also push a periodic refresh so the burn-rate ticks even when idle.
                    snap = store.snapshot(settings)
                    yield f"data: {json.dumps(snapshot_to_dict(snap))}\n\n"
                    now = asyncio.get_event_loop().time()
                    if now - last_heartbeat > 15:
                        yield ": ping\n\n"
                        last_heartbeat = now

        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", media_type="text/html")

    return app
