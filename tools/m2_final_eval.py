"""M2 P5 final acceptance: evaluate trained checkpoints on the frozen test set.

Runs each given checkpoint (5 independent initializations expected) greedily
over the 1,000 frozen test seeds at A0, plus the heuristic baseline on the
same seeds. Reports per-checkpoint win rates, the mean win rate with a 95%
bootstrap CI, and gate checks: mean >= 40%, CI lower bound above the
heuristic, illegal actions == 0, timeouts < 1%.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import random
import shutil
import sys
import threading
import time

import torch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "rl" / "src"))
from sts2rl.agent import PolicyAgent
from sts2rl.checkpoint import load_checkpoint
from sts2rl.curriculum import CurriculumStage
from sts2rl.engine import EngineClient, EngineTimeout, RunConfig
from sts2rl.entities import EntityVocab
from sts2rl.features import CANDIDATE_FEATURE_DIM
from sts2rl.model import EntityRecurrentPolicy
from sts2rl.policy import heuristic_action

CLI_ROOT = REPO_ROOT / "external" / "sts2-cli"
DLL = CLI_ROOT / "src" / "Sts2Headless" / "bin" / "Debug" / "net9.0" / "Sts2Headless.dll"
VOCAB_PATH = REPO_ROOT / "rl" / "schema" / "m2_vocab.json"
SPLIT_PATH = REPO_ROOT / "rl" / "seeds" / "m2_ironclad_seed_split.json"
FULL_RUN = CurriculumStage("full_a0")
MAX_STEPS = 2000


def make_client() -> EngineClient:
    dotnet = os.environ.get("DOTNET_HOST_PATH") or shutil.which("dotnet") or "dotnet"
    return EngineClient([dotnet, str(DLL)], cwd=CLI_ROOT,
                        timeout=float(os.environ.get("STS2_EVAL_TIMEOUT", "10")),
                        env={"STS2_GAME_DIR": os.environ["STS2_GAME_DIR"]})


def run_policy_episode(client: EngineClient, seed: str, agent: PolicyAgent,
                       lock: threading.Lock) -> dict:
    steps = 0
    start = time.perf_counter()
    try:
        state = client.reset(RunConfig("Ironclad", seed))
        hidden = None
        while steps < MAX_STEPS and state.phase != "game_over":
            with lock:
                step = agent.act(state.raw, state.candidates, hidden, greedy=True)
            hidden = step.hidden
            state = client.step(state.candidates[step.index]).state
            steps += 1
        return {"seed": seed, "outcome": state.raw.get("victory") if state.phase == "game_over" else None,
                "steps": steps, "phase": state.phase, "error": None,
                "seconds": round(time.perf_counter() - start, 2)}
    except Exception as exc:
        return {"seed": seed, "outcome": None, "steps": steps, "phase": None,
                "error": type(exc).__name__, "error_detail": str(exc)[:300],
                "seconds": round(time.perf_counter() - start, 2)}


def run_heuristic_episode(client: EngineClient, seed: str) -> dict:
    steps = 0
    try:
        state = client.reset(RunConfig("Ironclad", seed))
        while steps < MAX_STEPS and state.phase != "game_over":
            state = client.step(heuristic_action(state)).state
            steps += 1
        return {"seed": seed, "outcome": state.raw.get("victory") if state.phase == "game_over" else None,
                "steps": steps, "phase": state.phase, "error": None}
    except Exception as exc:
        return {"seed": seed, "outcome": None, "steps": steps, "phase": None,
                "error": type(exc).__name__}


def evaluate(seeds: list[str], workers: int, episode_fn) -> list[dict]:
    buckets = [seeds[i::workers] for i in range(workers)]
    rows: list[dict] = []

    def worker(bucket: list[str]) -> list[dict]:
        with make_client() as client:
            return [episode_fn(client, seed) for seed in bucket]

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for future in [pool.submit(worker, b) for b in buckets if b]:
            rows.extend(future.result())
    rows.sort(key=lambda r: seeds.index(r["seed"]))
    return rows


def summarize(rows: list[dict]) -> dict:
    wins = [1 if r["outcome"] is True else 0 for r in rows]
    return {
        "episodes": len(rows),
        "wins": sum(wins),
        "win_rate": sum(wins) / max(len(rows), 1),
        "errors": sum(r["error"] is not None for r in rows),
        "timeouts": sum(r["error"] == "EngineTimeout" for r in rows),
        "nonterminal": sum(r["phase"] != "game_over" and r["error"] is None for r in rows),
    }


def bootstrap_mean_ci(win_lists: list[list[int]], resamples: int = 10000, seed: int = 0) -> tuple[float, float]:
    """Bootstrap over seeds of the across-checkpoint mean win rate."""
    rng = random.Random(seed)
    n = len(win_lists[0])
    means = []
    for _ in range(resamples):
        indices = [rng.randrange(n) for _ in range(n)]
        means.append(sum(sum(w[i] for w in win_lists) / len(win_lists) for i in indices) / n)
    means.sort()
    return means[int(resamples * 0.025)], means[int(resamples * 0.975)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkpoints", nargs="+", type=Path)
    parser.add_argument("--workers", type=int, default=int(os.environ.get("STS2_EVAL_WORKERS", "6")))
    parser.add_argument("--seeds", type=int, default=1000)
    parser.add_argument("--hidden", type=int, default=128)
    parser.add_argument("--layers", type=int, default=2)
    parser.add_argument("--heads", type=int, default=4)
    parser.add_argument("--output", type=Path, default=REPO_ROOT / "rl" / "runs" / "m2_final_eval.json")
    parser.add_argument("--skip-heuristic", action="store_true")
    args = parser.parse_args()

    if not os.environ.get("STS2_GAME_DIR"):
        raise SystemExit("STS2_GAME_DIR required")
    split = json.loads(SPLIT_PATH.read_text(encoding="utf-8"))
    seeds = list(split["test_seeds"])[: args.seeds]
    vocab = EntityVocab.load(VOCAB_PATH)

    report: dict = {"test_seed_hash": split["test_seed_hash"], "seeds": len(seeds), "checkpoints": []}
    win_lists: list[list[int]] = []
    lock = threading.Lock()
    for path in args.checkpoints:
        model = EntityRecurrentPolicy(vocab_size=vocab.size, candidate_dim=CANDIDATE_FEATURE_DIM,
                                      hidden=args.hidden, heads=args.heads, layers=args.layers)
        load_checkpoint(path, model)
        model.eval()
        agent = PolicyAgent(model, vocab)
        rows = evaluate(seeds, args.workers,
                        lambda client, seed, a=agent: run_policy_episode(client, seed, a, lock))
        summary = summarize(rows)
        win_lists.append([1 if r["outcome"] is True else 0 for r in rows])
        report["checkpoints"].append({"path": str(path), **summary, "results": rows})
        print(json.dumps({"checkpoint": str(path), **summary}))

    mean_rate = sum(sum(w) / len(w) for w in win_lists) / len(win_lists)
    low, high = bootstrap_mean_ci(win_lists)
    report["mean_win_rate"] = mean_rate
    report["bootstrap_ci95"] = [low, high]

    if not args.skip_heuristic:
        heuristic_rows = evaluate(seeds, args.workers, run_heuristic_episode)
        report["heuristic"] = {**summarize(heuristic_rows), "results": heuristic_rows}
        print(json.dumps({"heuristic": summarize(heuristic_rows)}))

    timeouts = sum(c["timeouts"] for c in report["checkpoints"])
    episodes = sum(c["episodes"] for c in report["checkpoints"])
    heuristic_rate = report.get("heuristic", {}).get("win_rate", 0.0)
    report["gates"] = {
        "mean_win_rate_ge_40": mean_rate >= 0.40,
        "ci_lower_above_heuristic": low > heuristic_rate,
        "timeout_rate_lt_1pct": timeouts / max(episodes, 1) < 0.01,
        "errors": sum(c["errors"] for c in report["checkpoints"]),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"mean_win_rate": mean_rate, "ci95": [low, high], "gates": report["gates"]}))
    return 0 if all(v is True for k, v in report["gates"].items() if k != "errors") and report["gates"]["errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
