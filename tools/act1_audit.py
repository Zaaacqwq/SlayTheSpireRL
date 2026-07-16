"""Act 1 gate audit: greedily evaluate candidate checkpoints on the held-out
development seeds (act1-capped, on the real headless engine, with the no-op
circuit breaker) and report per-checkpoint clear rate + stability. Used to pick
the most stable checkpoint before advancing to full_a0.

Usage:
    STS2_GAME_DIR=... python tools/act1_audit.py <ckpt.pt> [<ckpt.pt> ...] \
        --seeds 450 --offset 50 --workers 10
"""
from __future__ import annotations
import argparse, json, os, pathlib, shutil, sys, threading, time
from concurrent.futures import ThreadPoolExecutor

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "rl" / "src"))
from sts2rl.agent import PolicyAgent
from sts2rl.checkpoint import load_checkpoint
from sts2rl.curriculum import CurriculumStage
from sts2rl.engine import EngineClient
from sts2rl.entities import EntityVocab
from sts2rl.features import CANDIDATE_FEATURE_DIM
from sts2rl.model import EntityRecurrentPolicy
from sts2rl.ppo import PPOConfig, run_episode

CLI = REPO / "external" / "sts2-cli"
DLL = CLI / "src" / "Sts2Headless" / "bin" / "Debug" / "net9.0" / "Sts2Headless.dll"
VOCAB = REPO / "rl" / "schema" / "m2_vocab.json"
SPLIT = REPO / "rl" / "seeds" / "m2_ironclad_seed_split.json"
STAGE = CurriculumStage("act1", max_act=1)   # terminal = reached act 2 = act1 cleared


def make_client() -> EngineClient:
    dotnet = os.environ.get("DOTNET_HOST_PATH") or shutil.which("dotnet") or "dotnet"
    return EngineClient([dotnet, str(DLL)], cwd=CLI,
                        timeout=float(os.environ.get("STS2_EVAL_TIMEOUT", "20")),
                        env={"STS2_GAME_DIR": os.environ["STS2_GAME_DIR"]})


def evaluate_checkpoint(path, seeds, workers, vocab, hidden, layers, heads):
    model = EntityRecurrentPolicy(vocab_size=vocab.size, candidate_dim=CANDIDATE_FEATURE_DIM,
                                  hidden=hidden, heads=heads, layers=layers)
    load_checkpoint(path, model); model.eval()
    agent = PolicyAgent(model, vocab)
    cfg = PPOConfig()
    lock = threading.Lock()
    rows = []

    def play_bucket(bucket):
        out = []
        with make_client() as client:
            for seed in bucket:
                r = run_episode(client, STAGE, seed, agent, cfg, greedy=True, inference_lock=lock)
                out.append({"seed": seed, "win": r.outcome is True, "floor": r.final_floor,
                            "truncated": r.truncated, "noop_steps": r.noop_steps,
                            "error": r.error})
        return out

    buckets = [seeds[i::workers] for i in range(workers)]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for f in [pool.submit(play_bucket, b) for b in buckets if b]:
            rows.extend(f.result())

    wins = sum(r["win"] for r in rows)
    errs = sum(1 for r in rows if r["error"])
    graded = [r for r in rows if not r["error"]]
    floors = sorted(r["floor"] for r in graded)
    return {
        "episodes": len(rows), "graded": len(graded), "wins": wins,
        "clear_rate": round(wins / max(len(graded), 1), 4),
        "errors": errs, "truncated": sum(1 for r in rows if r["truncated"]),
        "noop_steps_total": sum(r["noop_steps"] for r in rows),
        "seeds_with_noop": sum(1 for r in rows if r["noop_steps"] > 0),
        "steps_at_limit": sum(1 for r in rows if r["truncated"]),
        "median_floor": floors[len(floors) // 2] if floors else 0,
        "error_types": {},
        "rows": rows,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("checkpoints", nargs="+", type=pathlib.Path)
    ap.add_argument("--seeds", type=int, default=450)
    ap.add_argument("--offset", type=int, default=50, help="skip the gate-eval seeds")
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--hidden", type=int, default=256)
    ap.add_argument("--layers", type=int, default=4)
    ap.add_argument("--heads", type=int, default=8)
    ap.add_argument("--out", type=pathlib.Path, default=REPO / "rl" / "runs" / "act1_audit.json")
    args = ap.parse_args()
    if not os.environ.get("STS2_GAME_DIR"):
        raise SystemExit("STS2_GAME_DIR required")

    dev = json.loads(SPLIT.read_text(encoding="utf-8"))["development_seeds"]
    seeds = dev[args.offset:args.offset + args.seeds]
    vocab = EntityVocab.load(VOCAB)
    report = {"seed_pool": "development", "offset": args.offset, "seeds": len(seeds),
              "checkpoints": {}}
    print(f"[audit] {len(seeds)} held-out dev seeds x {len(args.checkpoints)} checkpoints, "
          f"{args.workers} workers", flush=True)
    for path in args.checkpoints:
        t0 = time.perf_counter()
        summary = evaluate_checkpoint(path, seeds, args.workers, vocab,
                                      args.hidden, args.layers, args.heads)
        summary["seconds"] = round(time.perf_counter() - t0, 1)
        report["checkpoints"][path.name] = summary
        print(json.dumps({"checkpoint": path.name,
                          **{k: summary[k] for k in ("clear_rate", "wins", "graded",
                                                     "errors", "truncated", "median_floor",
                                                     "seeds_with_noop", "seconds")}}), flush=True)
        args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")  # incremental save

    best = max(report["checkpoints"].items(), key=lambda kv: kv[1]["clear_rate"])
    print(f"[audit] most-stable by clear_rate: {best[0]} @ {best[1]['clear_rate']:.1%}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
