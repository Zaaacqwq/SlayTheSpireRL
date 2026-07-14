"""Keep a training run alive across the things that killed it today.

In one day this run died three times and every time a human had to notice: a
machine reboot, a GPU driver update that invalidated the CUDA context mid-run, and
a NaN weight that killed the inference thread. Each left twelve engine processes
idling and hours of GPU time on the floor.

It also guards the other failure from today: two trainers resumed from the same
checkpoint and ran in parallel on one GPU. That does not corrupt anything, but it
starves both runs, and a starved run tells a coherent, confident, entirely wrong
story about whether training is working.

Usage:
    python tools/train_watchdog.py --run-name m2_v6 -- --workers 12 --device cuda ...

Everything after ``--`` is passed to ``m2_train.py`` verbatim. ``--resume`` is
supplied by the watchdog from the newest checkpoint, so do not pass it yourself.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

REPO_ROOT = Path(__file__).resolve().parent.parent
TRAINER = REPO_ROOT / "tools" / "m2_train.py"


def latest_checkpoint(run_dir: Path) -> Path | None:
    checkpoints = sorted(run_dir.glob("ckpt_*.pt"))
    milestone = checkpoints[-1] if checkpoints else None
    resume = run_dir / "resume.pt"
    if resume.exists() and (
        milestone is None or resume.stat().st_mtime_ns >= milestone.stat().st_mtime_ns
    ):
        return resume
    return milestone


def other_trainers(run_name: str) -> list[int]:
    """PIDs of any m2_train already running — two on one GPU starve each other."""
    if sys.platform != "win32":
        return []
    query = (
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
        "Where-Object { $_.CommandLine -match 'm2_train' } | "
        "ForEach-Object { $_.ProcessId }"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", query],
            capture_output=True, text=True, timeout=30,
        ).stdout
    except Exception:
        return []
    return [int(line) for line in out.split() if line.strip().isdigit()]


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--run-name", required=True)
    parser.add_argument("--runs-root", type=Path, default=REPO_ROOT / "rl" / "runs")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--max-restarts", type=int, default=8,
                        help="consecutive fast failures before giving up")
    parser.add_argument("--min-uptime", type=float, default=180.0,
                        help="a run shorter than this counts as a crash loop, not progress")
    parser.add_argument("--allow-concurrent", action="store_true",
                        help="start even if another trainer is running (they will fight over the GPU)")
    parser.add_argument("trainer_args", nargs=argparse.REMAINDER,
                        help="args after `--` are passed to m2_train.py")
    args = parser.parse_args()

    passthrough = [a for a in args.trainer_args if a != "--"]
    if any(a == "--resume" for a in passthrough):
        raise SystemExit("do not pass --resume; the watchdog resumes from the newest checkpoint")

    existing = other_trainers(args.run_name)
    if existing and not args.allow_concurrent:
        raise SystemExit(
            f"another trainer is already running (PID {', '.join(map(str, existing))}). "
            f"Two trainers on one GPU starve each other and make a healthy run look "
            f"like it is collapsing. Kill it, or pass --allow-concurrent."
        )

    run_dir = args.runs_root / args.run_name
    log_path = args.runs_root / f"{args.run_name}.log"
    restarts = 0
    stalled_restarts = 0

    while True:
        checkpoint = latest_checkpoint(run_dir)
        command = [args.python, "-u", str(TRAINER),
                   "--run-name", args.run_name, "--runs-root", str(args.runs_root),
                   *passthrough]
        if checkpoint is not None:
            command += ["--resume", str(checkpoint)]

        note = {"watchdog": "starting", "resume": checkpoint.name if checkpoint else None,
                "restarts": restarts}
        print(json.dumps(note), flush=True)

        started = time.monotonic()
        with log_path.open("a", encoding="utf-8") as log:
            log.write(json.dumps(note) + "\n")
            log.flush()
            code = subprocess.call(command, stdout=log, stderr=subprocess.STDOUT)
        uptime = time.monotonic() - started

        if code == 0:
            print(json.dumps({"watchdog": "trainer finished cleanly"}), flush=True)
            return 0

        # A run that dies immediately is a crash loop; one that dies after hours is
        # the machine misbehaving, and resuming is exactly the right response.
        if uptime < args.min_uptime:
            restarts += 1
        else:
            restarts = 0

        newest_checkpoint = latest_checkpoint(run_dir)
        if newest_checkpoint == checkpoint:
            stalled_restarts += 1
        else:
            stalled_restarts = 0

        report = {"watchdog": "trainer died", "exit_code": code,
                  "uptime_s": round(uptime, 1), "consecutive_fast_failures": restarts,
                  "failures_without_checkpoint_progress": stalled_restarts}
        print(json.dumps(report), file=sys.stderr, flush=True)

        if restarts >= args.max_restarts or stalled_restarts >= args.max_restarts:
            reason = (
                f"{restarts} failures each under {args.min_uptime}s"
                if restarts >= args.max_restarts else
                f"{stalled_restarts} failures without a newer checkpoint"
            )
            print(json.dumps({
                "watchdog": "giving up",
                "reason": reason + " — this is a bug, not a flaky machine",
            }), file=sys.stderr, flush=True)
            return 1

        time.sleep(min(5 * restarts, 30))


if __name__ == "__main__":
    raise SystemExit(main())
