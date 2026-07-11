"""M2 P0: freeze the Ironclad A0 seed split and measure random/heuristic baselines.

Seeds live in the ``m2-a0-ironclad-<index>`` namespace and are partitioned by
``sts2rl.seeds.split_seed``. The first 1,000 test-split seeds are frozen to
``rl/seeds/m2_ironclad_seed_split.json`` and must never influence any decision
before the final M2 acceptance run. Baselines run on development-split seeds.
"""
from __future__ import annotations

import argparse
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
from sts2rl.policy import heuristic_action, random_action
from sts2rl.seeds import seed_hash, split_seed

CLI_ROOT = REPO_ROOT / "external" / "sts2-cli"
DLL = CLI_ROOT / "src" / "Sts2Headless" / "bin" / "Debug" / "net9.0" / "Sts2Headless.dll"
SPLIT_PATH = REPO_ROOT / "rl" / "seeds" / "m2_ironclad_seed_split.json"
CHARACTER = "Ironclad"
NAMESPACE = "m2-a0-ironclad"
TEST_SEED_COUNT = 1000
DEVELOPMENT_SEED_COUNT = 500
MAX_EPISODE_STEPS = 2000


def build_split() -> dict[str, object]:
    """Deterministically scan the namespace until 1,000 test seeds are frozen."""
    test: list[str] = []
    development: list[str] = []
    index = 0
    while len(test) < TEST_SEED_COUNT or len(development) < DEVELOPMENT_SEED_COUNT:
        seed = f"{NAMESPACE}-{index}"
        bucket = split_seed(seed)
        if bucket == "test" and len(test) < TEST_SEED_COUNT:
            test.append(seed)
        elif bucket == "development" and len(development) < DEVELOPMENT_SEED_COUNT:
            development.append(seed)
        index += 1
    return {
        "namespace": NAMESPACE,
        "character": CHARACTER,
        "ascension": 0,
        "scanned_indices": index,
        "test_seeds": test,
        "test_seed_hash": seed_hash(test),
        "development_seeds": development,
        "development_seed_hash": seed_hash(development),
        "note": "train seeds are every train-split seed in the namespace, generated on demand",
    }


def load_or_freeze_split() -> dict[str, object]:
    if SPLIT_PATH.exists():
        existing = json.loads(SPLIT_PATH.read_text(encoding="utf-8"))
        rebuilt = build_split()
        for key in ("test_seed_hash", "development_seed_hash"):
            if existing[key] != rebuilt[key]:
                raise SystemExit(f"frozen split at {SPLIT_PATH} no longer reproducible ({key} mismatch)")
        return existing
    split = build_split()
    SPLIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SPLIT_PATH.write_text(json.dumps(split, indent=2) + "\n", encoding="utf-8")
    return split


def run_episode(engine: EngineClient, seed: str, policy: str) -> dict[str, object]:
    steps = 0
    start = time.perf_counter()
    try:
        state = engine.reset(RunConfig(CHARACTER, seed))
        rng = random.Random(f"{policy}:{seed}")
        while steps < MAX_EPISODE_STEPS and state.phase != "game_over":
            action = random_action(state, rng) if policy == "random" else heuristic_action(state)
            state = engine.step(action).state
            steps += 1
        return {
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
            "seed": seed,
            "outcome": None,
            "steps": steps,
            "phase": None,
            "error": type(exc).__name__,
            "error_detail": str(exc)[:500],
            "seconds": round(time.perf_counter() - start, 2),
        }


def run_worker(seeds: list[str], policy: str) -> list[dict[str, object]]:
    with EngineClient(
        [os.environ.get("DOTNET_HOST_PATH") or shutil.which("dotnet") or "dotnet", str(DLL)],
        cwd=CLI_ROOT,
        timeout=float(os.environ.get("STS2_EVAL_TIMEOUT", "10")),
        env={"STS2_GAME_DIR": os.environ["STS2_GAME_DIR"]},
    ) as engine:
        return [run_episode(engine, seed, policy) for seed in seeds]


def bootstrap_ci(wins: list[int], resamples: int = 10000, seed: int = 0) -> tuple[float, float]:
    rng = random.Random(seed)
    n = len(wins)
    means = sorted(sum(rng.choice(wins) for _ in range(n)) / n for _ in range(resamples))
    return means[int(resamples * 0.025)], means[int(resamples * 0.975)]


def evaluate(policy: str, seeds: list[str], workers: int) -> dict[str, object]:
    buckets = [seeds[i::workers] for i in range(workers)]
    rows: list[dict[str, object]] = []
    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(run_worker, bucket, policy) for bucket in buckets if bucket]
        for future in as_completed(futures):
            rows.extend(future.result())
    rows.sort(key=lambda row: seeds.index(str(row["seed"])))
    wins = [1 if row["outcome"] is True else 0 for row in rows]
    errors = sum(row["error"] is not None for row in rows)
    timeouts = sum(row["error"] == "EngineTimeout" for row in rows)
    nonterminal = sum(row["phase"] != "game_over" and row["error"] is None for row in rows)
    low, high = bootstrap_ci(wins)
    return {
        "policy": policy,
        "character": CHARACTER,
        "ascension": 0,
        "episodes": len(rows),
        "wins": sum(wins),
        "win_rate": sum(wins) / len(rows),
        "bootstrap_ci95": [low, high],
        "errors": errors,
        "timeouts": timeouts,
        "timeout_rate": timeouts / len(rows),
        "nonterminal": nonterminal,
        "seconds": round(time.perf_counter() - start, 1),
        "seed_hash": seed_hash(seeds),
        "results": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", choices=["random", "heuristic", "both"], default="both")
    parser.add_argument("--episodes", type=int, default=200, help="development seeds to evaluate")
    parser.add_argument("--workers", type=int, default=int(os.environ.get("STS2_EVAL_WORKERS", "6")))
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / "rl" / "runs")
    args = parser.parse_args()

    if not os.environ.get("STS2_GAME_DIR"):
        raise SystemExit("STS2_GAME_DIR required")
    if not DLL.exists():
        raise SystemExit(f"build first; missing {DLL}")
    if not 0 < args.episodes <= DEVELOPMENT_SEED_COUNT:
        raise SystemExit(f"--episodes must be within 1..{DEVELOPMENT_SEED_COUNT} (development split only)")

    split = load_or_freeze_split()
    seeds = list(split["development_seeds"])[: args.episodes]
    policies = ["random", "heuristic"] if args.policy == "both" else [args.policy]
    failed = False
    for policy in policies:
        report = evaluate(policy, seeds, args.workers)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        path = args.output_dir / f"m2_baseline_{policy}.json"
        path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({k: v for k, v in report.items() if k != "results"}, indent=2))
        if report["errors"] or report["nonterminal"]:
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
