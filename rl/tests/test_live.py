from __future__ import annotations

import json
from pathlib import Path
import threading

from sts2rl.live import LiveEventWriter


def _lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_live_writer_keeps_worker_streams_separate_and_updates_snapshot(tmp_path: Path):
    writer = LiveEventWriter(tmp_path, 12, flush_interval=0.01)

    def emit(worker_id: int):
        for step in range(20):
            writer.emit(worker_id, {
                "type": "action", "status": "running", "seed": f"seed-{worker_id}",
                "step": step, "action": {"cmd": "action", "action": "end_turn"},
            })

    threads = [threading.Thread(target=emit, args=(worker_id,)) for worker_id in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    writer.close()

    snapshot = json.loads((tmp_path / "live" / "workers.json").read_text(encoding="utf-8"))
    assert snapshot["worker_count"] == 12
    assert snapshot["session_id"] == writer.session_id
    assert len(snapshot["workers"]) == 12
    for worker_id in range(12):
        rows = _lines(tmp_path / "live" / f"worker_{worker_id:02d}.jsonl")
        assert rows
        assert {row["worker_id"] for row in rows} == {worker_id}
        assert [row["seq"] for row in rows] == sorted(row["seq"] for row in rows)
        assert all(row["session_id"] == writer.session_id for row in rows)


def test_live_writer_compacts_to_bounded_recent_history(tmp_path: Path):
    writer = LiveEventWriter(tmp_path, 1, max_events_per_worker=100, flush_interval=0.01)
    for step in range(350):
        writer.emit(0, {"type": "action", "status": "running", "step": step})
    writer.close()
    rows = _lines(tmp_path / "live" / "worker_00.jsonl")
    assert len(rows) <= 100
    assert rows[-1]["status"] == "stopped"
    assert rows[-1]["seq"] > rows[0]["seq"]


def test_disabled_live_writer_creates_no_files(tmp_path: Path):
    writer = LiveEventWriter(tmp_path, 12, enabled=False)
    writer.emit(0, {"type": "action"})
    writer.close()
    assert not (tmp_path / "live").exists()
