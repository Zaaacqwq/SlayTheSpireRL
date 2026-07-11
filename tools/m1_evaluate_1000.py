from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import random
import shutil
import sys
import time

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "rl" / "src"))
from sts2rl.engine import EngineClient, RunConfig


WORKERS = int(os.environ.get("STS2_EVAL_WORKERS", "6"))
TIMEOUT = float(os.environ.get("STS2_EVAL_TIMEOUT", "10"))
EPISODES_PER_CHARACTER = int(os.environ.get("STS2_EVAL_EPISODES_PER_CHARACTER", "200"))
ROOT = REPO_ROOT / "external" / "sts2-cli"
DLL = ROOT / "src" / "Sts2Headless" / "bin" / "Debug" / "net9.0" / "Sts2Headless.dll"
DOTNET = os.environ.get("DOTNET_HOST_PATH") or shutil.which("dotnet")
if not DOTNET:
    raise SystemExit("dotnet not found (set DOTNET_HOST_PATH)")
CMD = [DOTNET, str(DLL)]
CHARS = ["Ironclad", "Silent", "Defect", "Necrobinder", "Regent"]


def run_episode(engine: EngineClient, job: tuple[str, int]) -> dict[str, object]:
    character, index = job
    seed = f"m1-a0-{character}-{index}"
    steps = 0
    start = time.perf_counter()
    try:
        state = engine.reset(RunConfig(character, seed))
        rng = random.Random(f"{character}:{index}")
        while steps < 2000 and state.phase != "game_over":
            state = engine.step(rng.choice(state.candidates)).state
            steps += 1
        return {
            "character": character,
            "index": index,
            "seed": seed,
            "outcome": state.raw.get("victory") if state.phase == "game_over" else None,
            "steps": steps,
            "phase": state.phase,
            "error": None,
            "error_detail": None,
            "seconds": round(time.perf_counter() - start, 2),
        }
    except Exception as exc:
        return {
            "character": character,
            "index": index,
            "seed": seed,
            "outcome": None,
            "steps": steps,
            "phase": None,
            "error": type(exc).__name__,
            "error_detail": str(exc),
            "seconds": round(time.perf_counter() - start, 2),
        }


def run_worker(jobs: list[tuple[str, int]]) -> list[dict[str, object]]:
    # One persistent engine per worker both avoids repeated JIT/DLL startup and
    # exercises the M1 reset/episode-isolation contract.
    with EngineClient(
        CMD,
        cwd=ROOT,
        timeout=TIMEOUT,
        env={"STS2_GAME_DIR": os.environ["STS2_GAME_DIR"]},
    ) as engine:
        return [run_episode(engine, job) for job in jobs]


if __name__ == "__main__":
    if not os.environ.get("STS2_GAME_DIR"):
        raise SystemExit("STS2_GAME_DIR required")
    if not DLL.exists():
        raise SystemExit(f"build first; missing {DLL}")
    if WORKERS < 1:
        raise SystemExit("STS2_EVAL_WORKERS must be >= 1")

    jobs = [
        (character, index)
        for character in CHARS
        for index in range(EPISODES_PER_CHARACTER)
    ]
    buckets = [jobs[index::WORKERS] for index in range(WORKERS)]
    output: list[dict[str, object]] = []
    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [pool.submit(run_worker, bucket) for bucket in buckets if bucket]
        for future in as_completed(futures):
            output.extend(future.result())

    output.sort(key=lambda row: (CHARS.index(str(row["character"])), int(row["index"])))
    output_path = Path(os.environ.get("STS2_EVAL_OUTPUT", "rl/runs/m1_a0_1000.json"))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")

    for character in CHARS:
        rows = [row for row in output if row["character"] == character]
        print(
            character,
            len(rows),
            sum(row["outcome"] is True for row in rows),
            sum(row["error"] is not None for row in rows),
            sum(row["phase"] != "game_over" and row["error"] is None for row in rows),
        )
    print(
        "TOTAL",
        len(output),
        "ERRORS",
        sum(row["error"] is not None for row in output),
        "NONTERMINAL",
        sum(row["phase"] != "game_over" and row["error"] is None for row in output),
        "SECONDS",
        round(time.perf_counter() - start, 1),
    )
    if any(row["error"] is not None or row["phase"] != "game_over" for row in output):
        raise SystemExit(1)
