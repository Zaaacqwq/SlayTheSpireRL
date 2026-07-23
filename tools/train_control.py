"""Start, pause, and inspect an M2 training run.

The dashboard and this CLI intentionally share this module so there is one
definition of "running" and one reconstruction of the trainer command.

Examples:
    python tools/train_control.py status --run-name m2_v7_clean_init0
    python tools/train_control.py pause  --run-name m2_v7_clean_init0
    python tools/train_control.py start  --run-name m2_v7_clean_init0
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUNS_ROOT = REPO_ROOT / "rl" / "runs"
WATCHDOG = REPO_ROOT / "tools" / "train_watchdog.py"

VALUE_OPTIONS = (
    "workers", "iterations", "episodes_per_iteration", "eval_every", "eval_episodes",
    "stage", "max_stage", "hidden", "layers", "heads", "init_seed", "lr",
    "minibatch_size", "update_epochs", "target_kl", "hard_kl_stop", "entropy_coef",
    "act_clear_bonus", "device",
    "record_train_every", "record_train_episodes", "live_retention", "boss_mix",
    "boss_replay_min_loadouts", "boss_replay_buffer_size", "fixed_seed",
    "hp_shaping_coef", "combat_win_bonus", "elite_win_bonus", "boss_win_bonus",
)
FLAG_OPTIONS = (
    "terminal_only", "no_live_monitor", "on_policy_boss_replay", "all_act_regions",
    "allow_visibility_violations", "hp_shaping_combat",
)


def _option(name: str) -> str:
    return "--" + name.replace("_", "-")


def _safe_run_dir(runs_root: Path, run_name: str) -> Path:
    root = runs_root.resolve()
    candidate = (root / run_name).resolve()
    if candidate.parent != root or not run_name or run_name in {".", ".."}:
        raise ValueError("invalid run name")
    return candidate


def trainer_args_from_config(config: dict[str, Any]) -> list[str]:
    """Rebuild m2_train CLI flags from the run's persisted argparse values."""
    args: list[str] = []
    for name in VALUE_OPTIONS:
        value = config.get(name)
        if value is not None:
            args.extend((_option(name), str(value)))
    for name in FLAG_OPTIONS:
        if config.get(name) is True:
            args.append(_option(name))
    return args


def _python_executable() -> Path:
    candidates = (
        REPO_ROOT / "rl" / ".venv" / "Scripts" / "python.exe",
        REPO_ROOT / "rl" / ".venv" / "bin" / "python",
    )
    return next((path for path in candidates if path.exists()), Path(sys.executable))


def _steam_libraries() -> list[Path]:
    paths: list[Path] = []
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
                paths.append(Path(winreg.QueryValueEx(key, "SteamPath")[0]))
        except (ImportError, OSError):
            pass
    for steam in list(paths):
        manifest = steam / "steamapps" / "libraryfolders.vdf"
        if not manifest.exists():
            continue
        text = manifest.read_text(encoding="utf-8", errors="ignore")
        for value in re.findall(r'"path"\s+"([^"]+)"', text):
            path = Path(value.replace("\\\\", "\\"))
            if path not in paths:
                paths.append(path)
    return paths


def find_game_dir() -> Path:
    configured = os.environ.get("STS2_GAME_DIR")
    if configured and Path(configured).is_dir():
        return Path(configured)
    suffix = Path("steamapps") / "common" / "Slay the Spire 2" / "data_sts2_windows_x86_64"
    for library in _steam_libraries():
        candidate = library / suffix
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError(
        "STS2_GAME_DIR is not set and Slay the Spire 2 was not found in Steam libraries"
    )


def _windows_python_processes() -> list[dict[str, Any]]:
    command = (
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
        "Select-Object ProcessId,ParentProcessId,CommandLine | ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        capture_output=True, text=True, timeout=20, check=False,
    )
    if result.returncode or not result.stdout.strip():
        return []
    payload = json.loads(result.stdout)
    return payload if isinstance(payload, list) else [payload]


def _posix_python_processes() -> list[dict[str, Any]]:
    result = subprocess.run(
        ["ps", "-eo", "pid=,ppid=,args="], capture_output=True, text=True,
        timeout=10, check=False,
    )
    rows = []
    for line in result.stdout.splitlines():
        match = re.match(r"\s*(\d+)\s+(\d+)\s+(.*)", line)
        if match and "python" in match.group(3).lower():
            rows.append({
                "ProcessId": int(match.group(1)), "ParentProcessId": int(match.group(2)),
                "CommandLine": match.group(3),
            })
    return rows


def python_processes() -> list[dict[str, Any]]:
    return _windows_python_processes() if sys.platform == "win32" else _posix_python_processes()


def matching_processes(run_name: str, processes: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    processes = python_processes() if processes is None else processes
    run_pattern = re.compile(rf"--run-name(?:=|\s+)[\"']?{re.escape(run_name)}(?:[\"']?\s|$)")
    return [
        row for row in processes
        if run_pattern.search(str(row.get("CommandLine") or ""))
        and any(name in str(row.get("CommandLine") or "")
                for name in ("train_watchdog.py", "m2_train.py"))
    ]


def _last_iteration(run_dir: Path) -> int | None:
    history = run_dir / "history.jsonl"
    if not history.exists():
        return None
    last: int | None = None
    with history.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row.get("iteration"), int) and "train_win_rate" in row:
                last = row["iteration"]
    return last


def training_status(runs_root: Path, run_name: str) -> dict[str, Any]:
    run_dir = _safe_run_dir(runs_root, run_name)
    matches = matching_processes(run_name)
    trainer = [row for row in matches if "m2_train.py" in str(row.get("CommandLine") or "")]
    watchdog = [row for row in matches if "train_watchdog.py" in str(row.get("CommandLine") or "")]
    checkpoint = run_dir / "resume.pt"
    state = "running" if trainer else "starting" if watchdog else "stopped"
    return {
        "run_name": run_name,
        "state": state,
        "running": bool(trainer),
        "watchdog_pids": sorted({int(row["ProcessId"]) for row in watchdog}),
        "trainer_pids": sorted({int(row["ProcessId"]) for row in trainer}),
        "worker_count": int(_load_config(run_dir).get("workers", 0) or 0),
        "last_iteration": _last_iteration(run_dir),
        "checkpoint": checkpoint.name if checkpoint.exists() else None,
        "checkpoint_updated_at": (
            datetime.fromtimestamp(checkpoint.stat().st_mtime, timezone.utc).isoformat()
            if checkpoint.exists() else None
        ),
    }


def _load_config(run_dir: Path) -> dict[str, Any]:
    path = run_dir / "config.json"
    if not path.exists():
        raise FileNotFoundError(f"missing training config: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def start_training(runs_root: Path, run_name: str) -> dict[str, Any]:
    run_dir = _safe_run_dir(runs_root, run_name)
    current = training_status(runs_root, run_name)
    if current["state"] != "stopped":
        return current
    config = _load_config(run_dir)
    python = _python_executable()
    game_dir = find_game_dir()
    command = [
        str(python), "-u", str(WATCHDOG), "--run-name", run_name,
        "--runs-root", str(runs_root.resolve()), "--python", str(python), "--",
        *trainer_args_from_config(config),
    ]
    env = os.environ.copy()
    env["STS2_GAME_DIR"] = str(game_dir)
    stdout = (run_dir / "watchdog.stdout.log").open("a", encoding="utf-8")
    stderr = (run_dir / "watchdog.stderr.log").open("a", encoding="utf-8")
    kwargs: dict[str, Any] = {
        "cwd": REPO_ROOT, "env": env, "stdout": stdout, "stderr": stderr,
        "stdin": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    process = subprocess.Popen(command, **kwargs)
    stdout.close()
    stderr.close()
    (run_dir / "training-control.json").write_text(json.dumps({
        "pid": process.pid, "started_at": datetime.now(timezone.utc).isoformat(),
        "game_dir": str(game_dir),
    }, indent=2) + "\n", encoding="utf-8")
    for _ in range(20):
        time.sleep(0.15)
        status = training_status(runs_root, run_name)
        if status["state"] != "stopped":
            return status
    return training_status(runs_root, run_name)


def _terminate_tree(pid: int) -> None:
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True, text=True, timeout=20, check=False,
        )
    else:
        try:
            os.kill(pid, 15)
        except ProcessLookupError:
            pass


def pause_training(runs_root: Path, run_name: str) -> dict[str, Any]:
    matches = matching_processes(run_name)
    ids = {int(row["ProcessId"]) for row in matches}
    roots = [
        int(row["ProcessId"]) for row in matches
        if int(row.get("ParentProcessId") or 0) not in ids
    ]
    for pid in roots:
        _terminate_tree(pid)
    for _ in range(30):
        if not matching_processes(run_name):
            break
        time.sleep(0.1)
    return training_status(runs_root, run_name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("status", "start", "pause"))
    parser.add_argument("--run-name", required=True)
    parser.add_argument("--runs-root", type=Path, default=DEFAULT_RUNS_ROOT)
    args = parser.parse_args(argv)
    actions = {"status": training_status, "start": start_training, "pause": pause_training}
    try:
        payload = actions[args.action](args.runs_root, args.run_name)
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
