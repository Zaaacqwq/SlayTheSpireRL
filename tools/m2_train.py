"""M2 Ironclad curriculum trainer: recurrent masked PPO on the real engine.

Stages follow the roadmap ladder (normal combat -> mixed combat -> Act 1 ->
full A0). Training seeds come from the train split of the frozen
``m2-a0-ironclad`` namespace; development seeds gate stage advancement; the
1,000 frozen test seeds are never touched here.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import itertools
import json
import os
from pathlib import Path
import shutil
import sys
import threading
import time
from typing import Sequence

import torch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "rl" / "src"))
from sts2rl.agent import PolicyAgent
from sts2rl.artifacts import EpisodeArtifactWriter, IncrementalHistoryWriter
from sts2rl.batch_inference import BatchedAgent
from sts2rl.checkpoint import load_checkpoint, save_checkpoint
from sts2rl.curriculum import (
    CurriculumStage, Loadout, act_variant_of, boss_replay_split, ironclad_stages,
)
from sts2rl.engine import EngineClient, RunConfig
from sts2rl.entities import EntityVocab
from sts2rl.features import CANDIDATE_FEATURE_DIM
from sts2rl.logging import ExperimentLogger
from sts2rl.live import LiveEventWriter
from sts2rl.model import EntityRecurrentPolicy
from sts2rl.ppo import PPOConfig, finalize_episode, ppo_update_epoch, run_episode
from sts2rl.seeds import split_seed
from sts2rl.telemetry import action_mix, depth_profile, reward_health

# STS2_CLI_ROOT lets a secondary checkout (e.g. a git worktree without the
# built submodule) borrow the primary checkout's engine build.
CLI_ROOT = Path(os.environ.get("STS2_CLI_ROOT", REPO_ROOT / "external" / "sts2-cli"))
DLL = CLI_ROOT / "src" / "Sts2Headless" / "bin" / "Debug" / "net9.0" / "Sts2Headless.dll"
VOCAB_PATH = REPO_ROOT / "rl" / "schema" / "m2_vocab.json"
SPLIT_PATH = REPO_ROOT / "rl" / "seeds" / "m2_ironclad_seed_split.json"
NAMESPACE = "m2-a0-ironclad"

# Development win rates required to advance to the next stage. Combat-stage
# bars assume the [25, 80] random starting HP: sub-35-HP starts against hard
# regular/elite encounters are effectively unwinnable, so ~0.82 is the
# observed greedy ceiling on normal_combat (three flat evals at init 0).
ADVANCE_THRESHOLDS = {"normal_combat": 0.80, "mixed_combat": 0.60, "boss_combat": 0.30, "act1": 0.30}
LOADOUTS_PATH = REPO_ROOT / "rl" / "schema" / "m2_boss_loadouts.json"
MAX_EPISODE_ERROR_RATE = 0.05


def make_client() -> EngineClient:
    dotnet = os.environ.get("DOTNET_HOST_PATH") or shutil.which("dotnet") or "dotnet"
    return EngineClient(
        [dotnet, str(DLL)], cwd=CLI_ROOT,
        timeout=float(os.environ.get("STS2_TRAIN_TIMEOUT", "10")),
        env={"STS2_GAME_DIR": os.environ["STS2_GAME_DIR"]},
    )


def train_seed_stream(offset: int = 0):
    for index in itertools.count(offset):
        seed = f"{NAMESPACE}-train-{index}"
        if split_seed(seed) == "train":
            yield seed


def detect_act_variant(client: EngineClient, catalog: Sequence[dict], probes: int = 8) -> str | None:
    """Which Act 1 region does the engine actually hand out?

    Act 1 ships two disjoint regions and a run only ever visits one. start_run
    announces the act's boss at floor 1, so a handful of probes settle it without
    playing anything. Returns None when the probes disagree, which keeps the full
    pool rather than silently training on half a curriculum.
    """
    variants = set()
    for index in range(probes):
        state = client.reset(RunConfig("Ironclad", f"{NAMESPACE}-actprobe-{index}"))
        boss = ((state.raw.get("context") or {}).get("boss") or {}).get("id")
        if not boss:
            return None
        variants.add(act_variant_of(catalog, str(boss)))
    if len(variants) != 1:
        return None
    return next(iter(variants))


def collect_iteration(
    clients: list[EngineClient], stage: CurriculumStage, seeds: list[str],
    agent, config: PPOConfig, *, iteration: int,
    live: LiveEventWriter | None = None,
):
    # BatchedAgent is internally thread-safe; PolicyAgent needs the lock.
    lock = None if isinstance(agent, BatchedAgent) else threading.Lock()
    buckets = [seeds[i::len(clients)] for i in range(len(clients))]

    def worker(worker_id: int, client: EngineClient, bucket: list[str]):
        records = []
        for seed in bucket:
            callback = None if live is None else lambda event: live.emit(worker_id, {
                "iteration": iteration, "stage": stage.name, "split": "train", **event,
            })
            record = run_episode(
                client, stage, seed, agent, config, inference_lock=lock,
                live_callback=callback,
            )
            finalize_episode(record, config)
            records.append(record)
        return records

    records = []
    with ThreadPoolExecutor(max_workers=len(clients)) as pool:
        futures = [
            pool.submit(worker, worker_id, client, bucket)
            for worker_id, (client, bucket) in enumerate(zip(clients, buckets)) if bucket
        ]
        for future in futures:
            records.extend(future.result())
    return records


def evaluate_stage(
    clients: list[EngineClient], stage: CurriculumStage, seeds: list[str],
    agent, config: PPOConfig, *, iteration: int,
    live: LiveEventWriter | None = None,
) -> tuple[dict[str, float], list]:
    records = []
    lock = None if isinstance(agent, BatchedAgent) else threading.Lock()
    buckets = [seeds[i::len(clients)] for i in range(len(clients))]

    def worker(worker_id: int, client: EngineClient, bucket: list[str]):
        def play(seed: str):
            callback = None if live is None else lambda event: live.emit(worker_id, {
                "iteration": iteration, "stage": stage.name, "split": "dev", **event,
            })
            return run_episode(
                client, stage, seed, agent, config, inference_lock=lock, greedy=True,
                live_callback=callback,
            )
        return [
            play(seed) for seed in bucket
        ]

    with ThreadPoolExecutor(max_workers=len(clients)) as pool:
        futures = [
            pool.submit(worker, worker_id, client, bucket)
            for worker_id, (client, bucket) in enumerate(zip(clients, buckets)) if bucket
        ]
        for future in futures:
            records.extend(future.result())
    wins = sum(r.outcome is True for r in records)
    errors = sum(r.error is not None for r in records)
    return {
        "win_rate": wins / max(len(records), 1),
        "avg_floor": round(sum(r.final_floor for r in records) / max(len(records), 1), 2),
        "errors": errors,
        "episodes": len(records),
    }, records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-name", default=time.strftime("m2_%Y%m%d_%H%M%S"))
    parser.add_argument("--workers", type=int, default=int(os.environ.get("STS2_TRAIN_WORKERS", "6")))
    parser.add_argument("--iterations", type=int, default=1000)
    parser.add_argument("--episodes-per-iteration", type=int, default=48)
    parser.add_argument("--eval-every", type=int, default=10)
    parser.add_argument("--eval-episodes", type=int, default=50)
    parser.add_argument("--stage", default=None, help="start at this stage name")
    parser.add_argument("--max-stage", default=None, help="do not advance past this stage")
    parser.add_argument("--resume", type=Path, default=None)
    parser.add_argument("--hidden", type=int, default=128)
    parser.add_argument("--layers", type=int, default=2)
    parser.add_argument("--heads", type=int, default=4)
    parser.add_argument("--terminal-only", action="store_true", help="reward ablation: no floor shaping")
    parser.add_argument("--init-seed", type=int, default=0)
    parser.add_argument("--lr", type=float, default=3e-4)
    parser.add_argument("--device", default="cpu", help="cpu / cuda / mps")
    parser.add_argument("--runs-root", type=Path, default=REPO_ROOT / "rl" / "runs")
    parser.add_argument("--record-train-every", type=int, default=10,
                        help="record full train episodes every N iterations (0 disables)")
    parser.add_argument("--record-train-episodes", type=int, default=8,
                        help="max train episodes recorded per sampled iteration")
    parser.add_argument("--no-live-monitor", action="store_true",
                        help="disable bounded per-worker live dashboard telemetry")
    parser.add_argument("--live-retention", type=int, default=2000,
                        help="recent live events retained per worker")
    parser.add_argument("--boss-mix", type=float, default=0.15,
                        help="fraction of each run-stage iteration replayed as boss "
                             "fights, so the boss skill is not forgotten (0 disables)")
    parser.add_argument("--all-act-regions", action="store_true",
                        help="keep both Act 1 regions in the encounter pools; by default "
                             "only the region runs actually visit is trained on")
    args = parser.parse_args()
    if not 0.0 <= args.boss_mix < 1.0:
        raise SystemExit("--boss-mix must be in [0, 1)")

    if not os.environ.get("STS2_GAME_DIR"):
        raise SystemExit("STS2_GAME_DIR required")
    if not DLL.exists():
        raise SystemExit(f"build first; missing {DLL}")

    torch.manual_seed(args.init_seed)
    vocab = EntityVocab.load(VOCAB_PATH)
    split = json.loads(SPLIT_PATH.read_text(encoding="utf-8"))
    dev_seeds = list(split["development_seeds"])

    config = PPOConfig(
        learning_rate=args.lr,
        episodes_per_iteration=args.episodes_per_iteration,
        floor_shaping=not args.terminal_only,
    )
    device = torch.device(args.device)
    model = EntityRecurrentPolicy(
        vocab_size=vocab.size, candidate_dim=CANDIDATE_FEATURE_DIM,
        hidden=args.hidden, heads=args.heads, layers=args.layers,
    ).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=config.learning_rate)
    # On an accelerator, batch concurrent workers' decisions into one forward.
    agent = (BatchedAgent(model, vocab, max_batch=max(args.workers, 2))
             if device.type != "cpu" else PolicyAgent(model, vocab))

    run_dir = args.runs_root / args.run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    logger = ExperimentLogger(run_dir / "tb")
    episode_writer = EpisodeArtifactWriter(run_dir)
    history_writer = IncrementalHistoryWriter(run_dir / "history.jsonl")
    live = LiveEventWriter(
        run_dir, args.workers, enabled=not args.no_live_monitor,
        max_events_per_worker=args.live_retention,
    )
    (run_dir / "config.json").write_text(json.dumps({
        **vars(args), "resume": str(args.resume) if args.resume else None,
        "ppo": config.__dict__, "vocab_size": vocab.size,
        "dev_seed_hash": split["development_seed_hash"],
    }, indent=2, default=str) + "\n", encoding="utf-8")

    with make_client() as probe:
        catalog = probe.list_models("encounter")
        act_variant = None if args.all_act_regions else detect_act_variant(probe, catalog)
    boss_loadouts: tuple[Loadout, ...] = ()
    if LOADOUTS_PATH.exists():
        harvested = json.loads(LOADOUTS_PATH.read_text(encoding="utf-8"))
        boss_loadouts = tuple(
            Loadout(row["hp"], row["max_hp"], tuple(row["deck"]),
                    tuple(row["relics"]), tuple(row["potions"]))
            for row in harvested["loadouts"]
        )
    stages = ironclad_stages(catalog, boss_loadouts, act_variant=act_variant)
    boss_stage = next((s for s in stages if s.name == "boss_combat"), None)
    print(json.dumps({
        "act_variant": act_variant,
        "encounters": {s.name: len(s.encounters) for s in stages if s.is_combat},
        "boss_mix": args.boss_mix,
    }))
    clients = [make_client() for _ in range(args.workers)]
    stage_index = 0
    iteration_start = 0
    seed_cursor = 0

    if args.resume:
        payload = load_checkpoint(args.resume, model, optimizer)
        if payload.get("migrated_keys"):
            print(json.dumps({"resume_migrated": payload["migrated_keys"],
                              "optimizer_reset": bool(payload.get("optimizer_skipped"))}))
        stage_index = int(payload["config"].get("stage_index", 0))
        iteration_start = int(payload["step"]) + 1
        seed_cursor = int(payload["config"].get("seed_cursor", 0))
    if args.stage:
        stage_index = [s.name for s in stages].index(args.stage)
    max_stage_index = (
        [s.name for s in stages].index(args.max_stage) if args.max_stage else len(stages) - 1
    )

    seed_stream = train_seed_stream(seed_cursor)
    history: list[dict] = []
    try:
        for iteration in range(iteration_start, args.iterations):
            stage = stages[min(stage_index, max_stage_index)]
            seeds = []
            for seed in seed_stream:
                seeds.append(seed)
                seed_cursor += 1
                if len(seeds) >= config.episodes_per_iteration:
                    break
            start = time.perf_counter()
            # Anti-forgetting: a full run holds exactly one boss fight and only
            # ~60% of runs reach it, so boss gradient is diluted ~1:100 against
            # regular combat and the boss skill decays away (measured: bridge win
            # rate 23.3% -> 12.5% over 160 act1 iterations, collapsing hardest on
            # the two bosses real runs meet most). Keep replaying boss fights
            # while the run stages train.
            boss_seeds: list[str] = []
            if boss_stage is not None and not stage.is_combat and args.boss_mix > 0:
                boss_seeds, seeds = boss_replay_split(seeds, args.boss_mix)
            records = collect_iteration(
                clients, stage, seeds, agent, config, iteration=iteration, live=live,
            )
            boss_records = []
            if boss_seeds:
                boss_records = collect_iteration(
                    clients, boss_stage, boss_seeds, agent, config,
                    iteration=iteration, live=live,
                )
            # Full step-level records are large; sample train iterations so an
            # 800-iteration run stays browsable without flooding the disk.
            if args.record_train_every and iteration % args.record_train_every == 0:
                episode_writer.write_many(
                    records[: args.record_train_episodes], iteration=iteration,
                    stage=stage.name, split="train", character="Ironclad",
                )
            trained = records + boss_records
            errors = sum(r.error is not None for r in trained)
            if errors / max(len(trained), 1) > MAX_EPISODE_ERROR_RATE:
                for r in trained:
                    if r.error:
                        print(f"[error] seed={r.seed} {r.error}", file=sys.stderr)
                raise SystemExit(f"episode error rate too high: {errors}/{len(trained)}")
            live.set_all("updating", iteration=iteration, stage=stage.name, split="train")
            stats = ppo_update_epoch(model, optimizer, trained, vocab, config)
            for _ in range(config.update_epochs - 1):
                stats = ppo_update_epoch(model, optimizer, trained, vocab, config)
            wins = sum(r.outcome is True for r in records)
            steps_total = sum(len(r.steps) for r in trained)
            row = {
                "iteration": iteration, "stage": stage.name,
                "train_win_rate": wins / max(len(records), 1),
                "episodes": len(records), "errors": errors,
                "steps": steps_total,
                "avg_floor": round(sum(r.final_floor for r in records) / max(len(records), 1), 2),
                **stats,
                "seconds": round(time.perf_counter() - start, 1),
            }
            if boss_records:
                # Boss retention, tracked separately: it must not be averaged into
                # the stage score that gates advancement.
                row["boss_replay_episodes"] = len(boss_records)
                row["boss_replay_win_rate"] = round(
                    sum(r.outcome is True for r in boss_records) / len(boss_records), 4
                )

            # Diagnostics that would have caught the two bugs that survived to v5:
            # a potion action never once offered, and a reward function that paid
            # more for dying at the boss than for beating it.
            health = reward_health(records, config.gamma)
            row["reward_health"] = health
            row["action_mix"] = action_mix(records)
            if not stage.is_combat:
                row["depth"] = depth_profile(records)
            if health["inverted"]:
                print(
                    f"[REWARD INVERTED] iteration {iteration}: losing returns "
                    f"{health['loss_return']} but winning returns {health['win_return']} "
                    f"({health['win_episodes']} wins, {health['loss_episodes']} losses). "
                    f"The policy is being taught to lose — stop and fix the reward.",
                    file=sys.stderr, flush=True,
                )
            history.append(row)
            history_writer.append(row)
            print(json.dumps(row))
            for key in ("train_win_rate", "avg_floor", "loss", "policy_loss", "value_loss", "entropy"):
                logger.scalar(f"{stage.name}/{key}", float(row[key]), iteration)
            if "boss_replay_win_rate" in row:
                logger.scalar(f"{stage.name}/boss_replay_win_rate",
                              float(row["boss_replay_win_rate"]), iteration)
            for name, value in (("win_return", health["win_return"]),
                                ("loss_return", health["loss_return"])):
                if value is not None:
                    logger.scalar(f"{stage.name}/{name}", float(value), iteration)
            for action, fraction in row["action_mix"].items():
                logger.scalar(f"{stage.name}/action.{action}", float(fraction), iteration)
            if "depth" in row and row["depth"]:
                for key in ("reached_boss_rate", "boss_conversion"):
                    logger.scalar(f"{stage.name}/{key}", float(row["depth"][key]), iteration)

            if (iteration + 1) % args.eval_every == 0:
                evaluation, evaluation_records = evaluate_stage(
                    clients, stage, dev_seeds[: args.eval_episodes], agent, config,
                    iteration=iteration, live=live,
                )
                episode_writer.write_many(
                    evaluation_records, iteration=iteration, stage=stage.name,
                    split="dev", character="Ironclad",
                )
                logger.scalar(f"{stage.name}/dev_win_rate", evaluation["win_rate"], iteration)
                dev_row = {"iteration": iteration, "dev": evaluation, "stage": stage.name}
                if not stage.is_combat:
                    dev_row["dev"] = {**evaluation, **depth_profile(evaluation_records)}
                    for key in ("reached_boss_rate", "boss_conversion"):
                        if key in dev_row["dev"]:
                            logger.scalar(f"{stage.name}/dev_{key}",
                                          float(dev_row["dev"][key]), iteration)
                # Dev rows used to go to stdout only, never to history.jsonl, so every
                # dashboard reading a run with a history file showed an empty validation
                # series and a blank "best dev win rate" tile.
                history_writer.append(dev_row)
                print(json.dumps(dev_row))
                save_checkpoint(
                    run_dir / f"ckpt_{iteration:05d}.pt", model, optimizer,
                    step=iteration,
                    config={"stage_index": stage_index, "seed_cursor": seed_cursor,
                            "stage": stage.name, "dev_win_rate": evaluation["win_rate"],
                            "init_seed": args.init_seed, "terminal_only": args.terminal_only},
                )
                threshold = ADVANCE_THRESHOLDS.get(stage.name)
                if threshold is not None and evaluation["win_rate"] >= threshold and stage_index < max_stage_index:
                    stage_index += 1
                    print(json.dumps({"advanced_to": stages[stage_index].name, "iteration": iteration}))
    finally:
        if isinstance(agent, BatchedAgent):
            agent.close()
        for client in clients:
            client.close()
        logger.close()
        live.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
