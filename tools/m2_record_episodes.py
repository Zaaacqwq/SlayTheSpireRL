"""Replay saved checkpoints of an existing run and record full episodes.

Existing runs trained before the artifact pipeline have metrics but no
step-level episode records. This tool loads selected checkpoints, replays
frozen development seeds greedily on the real engine, and writes the same
Parquet-per-episode artifacts the trainer now produces, so the dashboard can
show how play evolved across training (same seeds, different checkpoints).
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import threading
import time

import torch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "rl" / "src"))
from sts2rl.agent import PolicyAgent
from sts2rl.artifacts import EpisodeArtifactWriter
from sts2rl.checkpoint import load_checkpoint
from sts2rl.curriculum import CurriculumStage, Loadout, ironclad_stages
from sts2rl.engine import EngineClient
from sts2rl.entities import EntityVocab
from sts2rl.features import CANDIDATE_FEATURE_DIM
from sts2rl.model import EntityRecurrentPolicy
from sts2rl.ppo import PPOConfig, run_episode

# STS2_CLI_ROOT lets a secondary checkout (e.g. a git worktree without the
# built submodule) borrow the primary checkout's engine build.
CLI_ROOT = Path(os.environ.get("STS2_CLI_ROOT", REPO_ROOT / "external" / "sts2-cli"))
DLL = CLI_ROOT / "src" / "Sts2Headless" / "bin" / "Debug" / "net9.0" / "Sts2Headless.dll"
VOCAB_PATH = REPO_ROOT / "rl" / "schema" / "m2_vocab.json"
SPLIT_PATH = REPO_ROOT / "rl" / "seeds" / "m2_ironclad_seed_split.json"
LOADOUTS_PATH = REPO_ROOT / "rl" / "schema" / "m2_boss_loadouts.json"


def make_client(timeout: float) -> EngineClient:
    dotnet = os.environ.get("DOTNET_HOST_PATH") or shutil.which("dotnet") or "dotnet"
    return EngineClient(
        [dotnet, str(DLL)], cwd=CLI_ROOT, timeout=timeout,
        env={"STS2_GAME_DIR": os.environ["STS2_GAME_DIR"]},
    )


def select_checkpoints(run_dir: Path, spec: str) -> list[Path]:
    ckpts = sorted(run_dir.glob("ckpt_*.pt"))
    if not ckpts:
        raise SystemExit(f"no checkpoints under {run_dir}")
    if spec == "milestones":
        picks = {0, len(ckpts) // 2, len(ckpts) - 1}
        return [ckpts[index] for index in sorted(picks)]
    if spec == "latest":
        return [ckpts[-1]]
    if spec == "all":
        return ckpts
    by_step = {int(path.stem.split("_")[1]): path for path in ckpts}
    selected = []
    for token in spec.split(","):
        step = int(token.strip())
        if step not in by_step:
            raise SystemExit(f"checkpoint step {step} not found; have {sorted(by_step)}")
        selected.append(by_step[step])
    return selected


def load_stages() -> tuple[CurriculumStage, ...]:
    with make_client(timeout=30.0) as probe:
        catalog = probe.list_models("encounter")
    boss_loadouts: tuple[Loadout, ...] = ()
    if LOADOUTS_PATH.exists():
        harvested = json.loads(LOADOUTS_PATH.read_text(encoding="utf-8"))
        boss_loadouts = tuple(
            Loadout(row["hp"], row["max_hp"], tuple(row["deck"]),
                    tuple(row["relics"]), tuple(row["potions"]))
            for row in harvested["loadouts"]
        )
    return ironclad_stages(catalog, boss_loadouts)


def recorded_iterations(run_dir: Path, split: str) -> set[int]:
    manifest = run_dir / "episodes" / "manifest.jsonl"
    if not manifest.exists():
        return set()
    seen = set()
    with manifest.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("split") == split and entry.get("iteration") is not None:
                seen.add(int(entry["iteration"]))
    return seen


def replay_checkpoint(
    clients: list[EngineClient], stage: CurriculumStage, seeds: list[str],
    agent: PolicyAgent, config: PPOConfig,
) -> list:
    lock = threading.Lock()
    buckets = [seeds[i::len(clients)] for i in range(len(clients))]
    records = []
    threads = []
    failures: list[BaseException] = []

    def worker(client: EngineClient, bucket: list[str]):
        try:
            for seed in bucket:
                record = run_episode(client, stage, seed, agent, config,
                                     inference_lock=lock, greedy=True)
                with lock:
                    records.append(record)
        except BaseException as exc:  # noqa: BLE001 - surfaced after join
            failures.append(exc)

    for client, bucket in zip(clients, buckets):
        if bucket:
            thread = threading.Thread(target=worker, args=(client, bucket))
            thread.start()
            threads.append(thread)
    for thread in threads:
        thread.join()
    if failures:
        raise failures[0]
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-name", required=True, help="existing run under the runs root")
    parser.add_argument("--runs-root", type=Path, default=REPO_ROOT / "rl" / "runs")
    parser.add_argument("--checkpoints", default="milestones",
                        help="milestones | latest | all | comma-separated steps (e.g. 9,379,769)")
    parser.add_argument("--episodes", type=int, default=12, help="dev seeds replayed per checkpoint")
    parser.add_argument("--stage", default=None, help="override stage (default: the checkpoint's own stage)")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--split", default="replay")
    parser.add_argument("--timeout", type=float, default=float(os.environ.get("STS2_TRAIN_TIMEOUT", "20")))
    parser.add_argument("--force", action="store_true", help="re-record checkpoints already in the manifest")
    args = parser.parse_args()

    if not os.environ.get("STS2_GAME_DIR"):
        raise SystemExit("STS2_GAME_DIR required")
    if not DLL.exists():
        raise SystemExit(f"build first; missing {DLL}")

    run_dir = args.runs_root / args.run_name
    config_path = run_dir / "config.json"
    if not config_path.exists():
        raise SystemExit(f"run config not found: {config_path}")
    run_config = json.loads(config_path.read_text(encoding="utf-8"))

    vocab = EntityVocab.load(VOCAB_PATH)
    if run_config.get("vocab_size") not in (None, vocab.size):
        raise SystemExit(
            f"vocab mismatch: run trained with {run_config['vocab_size']}, current {vocab.size}")
    split = json.loads(SPLIT_PATH.read_text(encoding="utf-8"))
    seeds = list(split["development_seeds"])[: args.episodes]

    model = EntityRecurrentPolicy(
        vocab_size=vocab.size, candidate_dim=CANDIDATE_FEATURE_DIM,
        hidden=int(run_config.get("hidden", 128)),
        heads=int(run_config.get("heads", 4)),
        layers=int(run_config.get("layers", 2)),
    )
    agent = PolicyAgent(model, vocab)
    config = PPOConfig()

    checkpoints = select_checkpoints(run_dir, args.checkpoints)
    already = set() if args.force else recorded_iterations(run_dir, args.split)
    stages = load_stages()
    stage_by_name = {stage.name: stage for stage in stages}
    writer = EpisodeArtifactWriter(run_dir)
    character = str(run_config.get("character", "Ironclad"))

    clients = [make_client(args.timeout) for _ in range(args.workers)]
    try:
        for ckpt_path in checkpoints:
            payload = load_checkpoint(ckpt_path, model)
            model.eval()
            step = int(payload["step"])
            if step in already:
                print(json.dumps({"checkpoint": ckpt_path.name, "skipped": "already recorded"}))
                continue
            stage_name = args.stage or payload["config"].get("stage")
            if stage_name not in stage_by_name:
                raise SystemExit(
                    f"stage {stage_name!r} from {ckpt_path.name} unknown; have {sorted(stage_by_name)}")
            stage = stage_by_name[stage_name]
            started = time.perf_counter()
            records = replay_checkpoint(clients, stage, seeds, agent, config)
            entries = writer.write_many(
                records, iteration=step, stage=stage.name, split=args.split,
                character=character,
            )
            wins = sum(record.outcome is True for record in records)
            errors = sum(record.error is not None for record in records)
            print(json.dumps({
                "checkpoint": ckpt_path.name, "iteration": step, "stage": stage.name,
                "episodes": len(entries), "wins": wins, "errors": errors,
                "seconds": round(time.perf_counter() - started, 1),
            }))
    finally:
        for client in clients:
            client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
