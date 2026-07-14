"""Bounded, non-blocking live telemetry for dashboard worker consoles."""
from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import queue
import threading
import time
from typing import Any, Mapping
import uuid


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class LiveEventWriter:
    """Persist compact worker events without ever blocking sampler threads.

    Events are batched by a daemon writer. Each worker keeps a bounded JSONL
    history while ``workers.json`` is atomically replaced with the latest
    status snapshot for cheap dashboard polling.
    """

    def __init__(
        self,
        run_dir: Path,
        worker_count: int,
        *,
        enabled: bool = True,
        max_events_per_worker: int = 2_000,
        max_queue: int = 10_000,
        flush_interval: float = 0.1,
    ):
        self.enabled = enabled
        self.worker_count = worker_count
        self.max_events_per_worker = max(100, max_events_per_worker)
        self.flush_interval = flush_interval
        self.live_dir = run_dir / "live"
        self.session_id = uuid.uuid4().hex
        self._queue: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=max_queue)
        self._lock = threading.Lock()
        self._seq = [0 for _ in range(worker_count)]
        self._dropped = 0
        self._counts = [0 for _ in range(worker_count)]
        self._recent_actions = [deque() for _ in range(worker_count)]
        self._states = [
            {"worker_id": index, "status": "idle", "seq": 0, "updated_at": _now()}
            for index in range(worker_count)
        ]
        self._thread: threading.Thread | None = None
        if not enabled:
            return
        self.live_dir.mkdir(parents=True, exist_ok=True)
        for index in range(worker_count):
            path = self._event_path(index)
            if path.exists():
                self._counts[index] = sum(1 for _ in path.open("r", encoding="utf-8", errors="ignore"))
        self._write_snapshot()
        self._thread = threading.Thread(target=self._serve, name="live-event-writer", daemon=True)
        self._thread.start()

    @property
    def dropped_events(self) -> int:
        with self._lock:
            return self._dropped

    def emit(self, worker_id: int, payload: Mapping[str, Any]) -> None:
        if not self.enabled:
            return
        if worker_id < 0 or worker_id >= self.worker_count:
            raise ValueError(f"worker_id out of range: {worker_id}")
        with self._lock:
            self._seq[worker_id] += 1
            seq = self._seq[worker_id]
        event = {
            "session_id": self.session_id,
            "seq": seq,
            "timestamp": _now(),
            "worker_id": worker_id,
            **dict(payload),
        }
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            with self._lock:
                self._dropped += 1

    def set_all(self, status: str, **payload: Any) -> None:
        for worker_id in range(self.worker_count):
            self.emit(worker_id, {"type": "status", "status": status, **payload})

    def close(self) -> None:
        if not self.enabled or self._thread is None:
            return
        self.set_all("stopped")
        # The queue is bounded; make room for the sentinel without blocking the
        # training process indefinitely during shutdown.
        try:
            self._queue.put(None, timeout=1.0)
        except queue.Full:
            pass
        self._thread.join(timeout=5.0)
        self._thread = None

    def _event_path(self, worker_id: int) -> Path:
        return self.live_dir / f"worker_{worker_id:02d}.jsonl"

    def _serve(self) -> None:
        stopping = False
        while not stopping:
            batch: list[dict[str, Any]] = []
            try:
                first = self._queue.get(timeout=self.flush_interval)
                if first is None:
                    stopping = True
                else:
                    batch.append(first)
            except queue.Empty:
                pass
            deadline = time.monotonic() + self.flush_interval
            while not stopping and len(batch) < 1_000 and time.monotonic() < deadline:
                try:
                    event = self._queue.get_nowait()
                except queue.Empty:
                    break
                if event is None:
                    stopping = True
                    break
                batch.append(event)
            if batch:
                self._write_batch(batch)
            if stopping:
                # Drain already queued events before exiting.
                while True:
                    try:
                        event = self._queue.get_nowait()
                    except queue.Empty:
                        break
                    if event is not None:
                        self._write_batch([event])
                self._write_snapshot()

    def _write_batch(self, batch: list[dict[str, Any]]) -> None:
        grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for event in batch:
            worker_id = int(event["worker_id"])
            grouped[worker_id].append(event)
            if event.get("type") == "action":
                self._recent_actions[worker_id].append(time.monotonic())
            self._states[worker_id] = self._state_from_event(event)
        for worker_id, events in grouped.items():
            with self._event_path(worker_id).open("a", encoding="utf-8") as handle:
                for event in events:
                    handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
            self._counts[worker_id] += len(events)
            # Compact in chunks: retain exactly the configured recent window,
            # while avoiding a full-file rewrite for every subsequent event.
            if self._counts[worker_id] > self.max_events_per_worker + max(100, self.max_events_per_worker // 4):
                self._compact(worker_id)
        self._write_snapshot()

    def _state_from_event(self, event: Mapping[str, Any]) -> dict[str, Any]:
        prior = self._states[int(event["worker_id"])]
        state = {**prior, **dict(event)}
        # The event type is useful in history, but status cards have a stable
        # status field and do not need it duplicated.
        state.pop("type", None)
        return state

    def _compact(self, worker_id: int) -> None:
        path = self._event_path(worker_id)
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        kept = lines[-self.max_events_per_worker:]
        temp = path.with_suffix(".jsonl.tmp")
        temp.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
        os.replace(temp, path)
        self._counts[worker_id] = len(kept)

    def _write_snapshot(self) -> None:
        cutoff = time.monotonic() - 5.0
        for worker_id, timestamps in enumerate(self._recent_actions):
            while timestamps and timestamps[0] < cutoff:
                timestamps.popleft()
            self._states[worker_id]["action_rate"] = round(len(timestamps) / 5.0, 2)
        snapshot = {
            "enabled": True,
            "session_id": self.session_id,
            "updated_at": _now(),
            "worker_count": self.worker_count,
            "dropped_events": self.dropped_events,
            "action_rate": round(sum(len(items) for items in self._recent_actions) / 5.0, 2),
            "max_events_per_worker": self.max_events_per_worker,
            "workers": self._states,
        }
        target = self.live_dir / "workers.json"
        temp = target.with_suffix(".json.tmp")
        temp.write_text(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        os.replace(temp, target)
