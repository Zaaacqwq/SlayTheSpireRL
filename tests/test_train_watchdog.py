from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "train_watchdog", REPO_ROOT / "tools" / "train_watchdog.py"
)
watchdog = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(watchdog)


def test_latest_checkpoint_orders_numerically_not_lexically(tmp_path):
    for step in (9, 99, 389, 1000):
        (tmp_path / f"ckpt_{step:05d}.pt").touch()
    assert watchdog.latest_checkpoint(tmp_path).name == "ckpt_01000.pt"


def test_no_checkpoint_yet_is_a_cold_start_not_an_error(tmp_path):
    assert watchdog.latest_checkpoint(tmp_path) is None


def test_latest_checkpoint_prefers_newer_per_iteration_resume(tmp_path):
    milestone = tmp_path / "ckpt_00079.pt"
    resume = tmp_path / "resume.pt"
    milestone.touch()
    resume.touch()
    assert watchdog.latest_checkpoint(tmp_path) == resume


def test_refuses_to_start_beside_another_trainer(tmp_path, monkeypatch, capsys):
    # Two trainers on one GPU starve each other. The starved run then reports a
    # collapsing avg_floor and a flat win rate, which reads exactly like a broken
    # reward function — it cost most of a day and nearly reverted a correct fix.
    monkeypatch.setattr(watchdog, "other_trainers", lambda _run: [1916, 9896])
    monkeypatch.setattr(sys, "argv", [
        "train_watchdog.py", "--run-name", "m2_v6", "--runs-root", str(tmp_path),
    ])
    with pytest.raises(SystemExit) as exit_info:
        watchdog.main()
    assert "1916" in str(exit_info.value) and "9896" in str(exit_info.value)


def test_allow_concurrent_is_an_explicit_opt_in(tmp_path, monkeypatch):
    monkeypatch.setattr(watchdog, "other_trainers", lambda _run: [1916])
    calls: list[list[str]] = []

    def fake_call(command, **_kwargs):
        calls.append(command)
        return 0  # trainer finishes cleanly

    monkeypatch.setattr(watchdog.subprocess, "call", fake_call)
    monkeypatch.setattr(sys, "argv", [
        "train_watchdog.py", "--run-name", "m2_v6", "--runs-root", str(tmp_path),
        "--allow-concurrent",
    ])
    assert watchdog.main() == 0
    assert calls, "the trainer should have been launched"


def test_resume_is_supplied_by_the_watchdog_not_the_caller(tmp_path, monkeypatch):
    monkeypatch.setattr(watchdog, "other_trainers", lambda _run: [])
    monkeypatch.setattr(sys, "argv", [
        "train_watchdog.py", "--run-name", "m2_v6", "--runs-root", str(tmp_path),
        "--", "--resume", "whatever.pt",
    ])
    with pytest.raises(SystemExit, match="do not pass --resume"):
        watchdog.main()


def test_restarts_from_the_newest_checkpoint_after_a_crash(tmp_path, monkeypatch):
    run_dir = tmp_path / "m2_v6"
    run_dir.mkdir()
    (run_dir / "ckpt_00389.pt").touch()
    monkeypatch.setattr(watchdog, "other_trainers", lambda _run: [])
    monkeypatch.setattr(watchdog.time, "sleep", lambda _s: None)

    commands: list[list[str]] = []
    codes = iter([1, 0])  # die once, then finish

    def fake_call(command, **_kwargs):
        commands.append(command)
        return next(codes)

    monkeypatch.setattr(watchdog.subprocess, "call", fake_call)
    monkeypatch.setattr(sys, "argv", [
        "train_watchdog.py", "--run-name", "m2_v6", "--runs-root", str(tmp_path),
        "--", "--workers", "12",
    ])
    assert watchdog.main() == 0
    assert len(commands) == 2
    for command in commands:
        assert "--resume" in command
        assert command[command.index("--resume") + 1].endswith("ckpt_00389.pt")
        assert command[command.index("--workers") + 1] == "12"


def test_gives_up_on_a_crash_loop_instead_of_restarting_forever(tmp_path, monkeypatch):
    monkeypatch.setattr(watchdog, "other_trainers", lambda _run: [])
    monkeypatch.setattr(watchdog.time, "sleep", lambda _s: None)
    monkeypatch.setattr(watchdog.subprocess, "call", lambda *_a, **_k: 1)
    monkeypatch.setattr(sys, "argv", [
        "train_watchdog.py", "--run-name", "m2_v6", "--runs-root", str(tmp_path),
        "--max-restarts", "3",
    ])
    # every attempt dies instantly, so uptime < min-uptime and the counter climbs
    assert watchdog.main() == 1


def test_gives_up_when_long_failures_make_no_checkpoint_progress(tmp_path, monkeypatch):
    run_dir = tmp_path / "m2_v7"
    run_dir.mkdir()
    (run_dir / "ckpt_00079.pt").touch()
    monkeypatch.setattr(watchdog, "other_trainers", lambda _run: [])
    monkeypatch.setattr(watchdog.time, "sleep", lambda _s: None)
    monkeypatch.setattr(watchdog.subprocess, "call", lambda *_a, **_k: 1)
    monkeypatch.setattr(sys, "argv", [
        "train_watchdog.py", "--run-name", "m2_v7", "--runs-root", str(tmp_path),
        "--min-uptime", "0", "--max-restarts", "2",
    ])
    # Uptime never counts as a fast failure, but retrying the identical
    # checkpoint is still a deterministic crash loop and must terminate.
    assert watchdog.main() == 1
