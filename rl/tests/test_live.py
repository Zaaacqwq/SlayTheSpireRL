from __future__ import annotations

import json
from pathlib import Path
import threading
import time

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


def test_a_locked_snapshot_does_not_kill_the_writer_thread(tmp_path):
    """Windows refuses os.replace while a reader holds the target open.

    POSIX rename does not care. Windows raises WinError 5 unless the reader used
    FILE_SHARE_DELETE, which the dashboard's plain open() does not — so polling
    workers.json during training killed the writer thread outright, and every
    worker console went dark for the rest of the run.
    """
    writer = LiveEventWriter(tmp_path, worker_count=2, flush_interval=0.01)
    try:
        writer.emit(0, {"type": "status", "status": "running"})
        _wait_for(lambda: (tmp_path / "live" / "workers.json").exists())

        # hold the snapshot open the way the dashboard does, then keep writing
        with (tmp_path / "live" / "workers.json").open("r", encoding="utf-8") as reader:
            reader.read()
            for i in range(30):
                writer.emit(i % 2, {"type": "action", "step": i})
            time.sleep(0.3)
            assert writer._thread is not None and writer._thread.is_alive(), (
                "a locked snapshot must cost events, not the telemetry stream"
            )

        # and it recovers once the reader lets go
        writer.emit(0, {"type": "status", "status": "still here"})
        _wait_for(lambda: "still here" in (tmp_path / "live" / "workers.json").read_text(encoding="utf-8"))
    finally:
        writer.close()


def test_atomic_replace_reports_failure_instead_of_raising(tmp_path, monkeypatch):
    from sts2rl.live import _atomic_replace

    temp = tmp_path / "x.tmp"
    temp.write_text("payload", encoding="utf-8")
    target = tmp_path / "x.json"

    monkeypatch.setattr(
        "sts2rl.live.os.replace",
        lambda *_a: (_ for _ in ()).throw(PermissionError("[WinError 5] Access is denied")),
    )
    assert _atomic_replace(temp, target, attempts=2) is False
    assert not temp.exists(), "a failed replace must not leave its temp file behind"


def _wait_for(condition, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if condition():
                return
        except OSError:
            pass
        time.sleep(0.02)
    raise AssertionError("condition not met within timeout")
