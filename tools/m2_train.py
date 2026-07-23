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
    CurriculumStage, Loadout, act_variant_of, boss_loadout_from_state,
    boss_replay_split, ironclad_stages,
)
from sts2rl.engine import EngineClient, RunConfig
from sts2rl.entities import EntityVocab
from sts2rl.features import CANDIDATE_FEATURE_DIM
from sts2rl.logging import ExperimentLogger
from sts2rl.live import LiveEventWriter
from sts2rl.model import EntityRecurrentPolicy
from sts2rl.ppo import PPOConfig, finalize_episode, ppo_update_epoch, run_episode
from sts2rl.seeds import split_seed
from sts2rl.sim import SimCombatBackend, ZhiyueRunBackend, discover_sim_vocab
from sts2rl.telemetry import action_mix, depth_profile, reward_health
from sts2rl.visibility import visibility_audit

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


def stage_index_after_evaluation(
    stage_index: int, max_stage_index: int, stage_name: str, win_rate: float,
) -> int:
    """Return the stage that a checkpoint must resume after this gate."""
    threshold = ADVANCE_THRESHOLDS.get(stage_name)
    if (threshold is not None and win_rate >= threshold
            and stage_index < max_stage_index):
        return stage_index + 1
    return stage_index


def _loadout_dict(loadout: Loadout) -> dict:
    return {
        "hp": loadout.hp, "max_hp": loadout.max_hp,
        "deck": list(loadout.deck), "relics": list(loadout.relics),
        "potions": list(loadout.potions), "encounter": loadout.encounter,
    }


def _loadout_from_dict(row: dict) -> Loadout:
    return Loadout(
        int(row["hp"]), int(row["max_hp"]), tuple(row["deck"]),
        tuple(row["relics"]), tuple(row["potions"]), row.get("encounter"),
    )


def update_on_policy_boss_buffer(buffer: list[Loadout], records, max_size: int) -> int:
    """Append one start-of-boss snapshot per run and retain a rolling window."""
    added = 0
    seen = {
        (row.encounter, row.hp, row.max_hp, row.deck, row.relics, row.potions)
        for row in buffer
    }
    for record in records:
        for step in record.steps:
            loadout = boss_loadout_from_state(step.raw_state)
            if loadout is None:
                continue
            key = (loadout.encounter, loadout.hp, loadout.max_hp,
                   loadout.deck, loadout.relics, loadout.potions)
            if key not in seen:
                buffer.append(loadout)
                seen.add(key)
                added += 1
            break
    if len(buffer) > max_size:
        del buffer[:-max_size]
    return added


def save_on_policy_boss_buffer(path: Path, buffer: Sequence[Loadout]) -> None:
    payload = {"version": 1, "source": "on_policy", "loadouts": [
        _loadout_dict(row) for row in buffer
    ]}
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def make_client() -> EngineClient:
    dotnet = os.environ.get("DOTNET_HOST_PATH") or shutil.which("dotnet") or "dotnet"
    return EngineClient(
        [dotnet, str(DLL)], cwd=CLI_ROOT,
        timeout=float(os.environ.get("STS2_TRAIN_TIMEOUT", "10")),
        env={"STS2_GAME_DIR": os.environ["STS2_GAME_DIR"]},
    )


def make_combat_backend(kind: str):
    """One combat execution backend: real engine subprocess, or the validated
    in-process zhiyue simulator. Both expose the EngineClient combat surface
    (``reset_combat`` / ``step`` / ``list_models`` / ``close`` / context
    manager) that ``run_episode`` drives for combat stages."""
    if kind == "zhiyue":
        # Dual-mode: combat stages via the inner SimCombatBackend, run stages
        # (act1/full_a0) via the wrapped RunManager.
        return ZhiyueRunBackend()
    return SimCombatBackend() if kind == "sim" else make_client()


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
    parser.add_argument("--entropy-coef", type=float, default=None,
                        help="PPO entropy bonus (default: PPOConfig default). "
                             "Lower (~0.003) for sparse multi-act stages to resist "
                             "entropy drifting up / unlearning; 0.01 for single-act1.")
    parser.add_argument("--act-clear-bonus", type=float, default=None,
                        help="Direct reward each time an act boss is beaten (act index "
                             "advances). Needed on multi-act stages (acts_12/full_a0) where "
                             "potential shaping gives clearing act1 no signal; 1.0 matches "
                             "the +1 the act1 stage paid. Leave 0.0 for single-act1 runs.")
    parser.add_argument("--hp-shaping-coef", type=float, default=None,
                        help="Potential-based HP-preservation shaping (0 disables, the "
                             "default). Phi = coef * hp/max_hp, policy-invariant like floor "
                             "shaping, so it densifies the credit for chip damage without "
                             "changing the optimum. Aimed at act-2 attrition; ~0.3 keeps it "
                             "below the +/-1 terminal and +1 act-clear bonus.")
    parser.add_argument("--init-seed", type=int, default=0)
    parser.add_argument("--lr", type=float, default=3e-4)
    parser.add_argument("--minibatch-size", type=int, default=256)
    parser.add_argument("--update-epochs", type=int, default=4)
    parser.add_argument("--target-kl", type=float, default=None)
    parser.add_argument("--hard-kl-stop", type=float, default=None)
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
    parser.add_argument("--on-policy-boss-replay", action=argparse.BooleanOptionalAction,
                        default=None,
                        help="build boss replay loadouts only from this run's own "
                             "first-round boss states; defaults on for zhiyue and off "
                             "for the real backend")
    parser.add_argument("--boss-replay-min-loadouts", type=int, default=8,
                        help="minimum on-policy snapshots before boss replay activates")
    parser.add_argument("--boss-replay-buffer-size", type=int, default=256,
                        help="maximum rolling on-policy boss snapshots retained")
    parser.add_argument("--all-act-regions", action="store_true",
                        help="keep both Act 1 regions in the encounter pools; by default "
                             "only the region runs actually visit is trained on")
    parser.add_argument("--allow-visibility-violations", action="store_true",
                        help="diagnostic escape hatch; production training must leave this off")
    parser.add_argument("--combat-backend", choices=("real", "sim", "zhiyue"), default="real",
                        help="execution backend. 'sim' is combat-only; 'zhiyue' also "
                             "serves act1/full-run stages through RunManager")
    parser.add_argument("--eval-backend", choices=("same", "real"), default="same",
                        help="backend for dev evaluations; 'real' scores on the ground-truth "
                             "engine even while sampling from the simulator.")
    parser.add_argument("--vocab-source", choices=("auto", "fixed", "sim"), default="auto",
                        help="entity vocabulary source. auto uses deterministic sim discovery "
                             "for sim/zhiyue and the checked-in fixed vocab for real")
    parser.add_argument("--sim-vocab-rollouts", type=int, default=16,
                        help="high-HP Act 1 discovery runs used to append simulator-native ids")
    args = parser.parse_args()
    if args.on_policy_boss_replay is None:
        args.on_policy_boss_replay = args.combat_backend == "zhiyue"
    if not 0.0 <= args.boss_mix < 1.0:
        raise SystemExit("--boss-mix must be in [0, 1)")
    if args.boss_replay_min_loadouts < 1:
        raise SystemExit("--boss-replay-min-loadouts must be positive")
    if args.boss_replay_buffer_size < args.boss_replay_min_loadouts:
        raise SystemExit(
            "--boss-replay-buffer-size must be >= --boss-replay-min-loadouts"
        )
    if args.sim_vocab_rollouts < 0:
        raise SystemExit("--sim-vocab-rollouts must be non-negative")
    if (args.combat_backend == "zhiyue" and not args.on_policy_boss_replay
            and args.boss_mix > 0):
        raise SystemExit(
            "zhiyue boss replay requires --on-policy-boss-replay; otherwise set --boss-mix 0"
        )

    # The real engine (game data + built DLL) is only needed when a real backend
    # is actually used; sim/zhiyue-only training runs on machines without the game.
    real_needed = args.combat_backend == "real" or args.eval_backend == "real"
    if real_needed and not os.environ.get("STS2_GAME_DIR"):
        raise SystemExit("STS2_GAME_DIR required")
    if real_needed and not DLL.exists():
        raise SystemExit(f"build first; missing {DLL}")

    run_dir = args.runs_root / args.run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    resolved_vocab_source = (
        "sim" if args.vocab_source == "auto"
        and args.combat_backend in ("sim", "zhiyue")
        else "fixed" if args.vocab_source == "auto" else args.vocab_source
    )
    run_vocab_path = run_dir / "entity_vocab.json"
    if run_vocab_path.exists():
        vocab = EntityVocab.load(run_vocab_path)
        if resolved_vocab_source == "sim":
            vocab, vocab_report = discover_sim_vocab(
                vocab,
                run_rollouts=args.sim_vocab_rollouts,
                seed=args.init_seed,
            )
            vocab_report = {
                **vocab_report, "source": "sim_refresh",
                "path": str(run_vocab_path),
            }
            vocab.save(run_vocab_path)
        else:
            vocab_report = {"source": "persisted", "path": str(run_vocab_path)}
    elif resolved_vocab_source == "sim":
        vocab, vocab_report = discover_sim_vocab(
            EntityVocab.load(VOCAB_PATH),
            run_rollouts=args.sim_vocab_rollouts,
            seed=args.init_seed,
        )
        vocab.save(run_vocab_path)
    else:
        vocab = EntityVocab.load(VOCAB_PATH)
        vocab.save(run_vocab_path)
        vocab_report = {"source": "fixed", "path": str(VOCAB_PATH)}
    print(json.dumps({"vocab": {**vocab_report, "size": vocab.size}}))

    torch.manual_seed(args.init_seed)
    split = json.loads(SPLIT_PATH.read_text(encoding="utf-8"))
    dev_seeds = list(split["development_seeds"])

    # entropy_coef / act_clear_bonus have no dataclass-default override path other
    # than these flags; leaving them None keeps PPOConfig's defaults so single-act1
    # runs are unaffected. Passing them writes the value into config.json for
    # reproducibility (the checkpoint's config.json is not replayed on resume).
    reward_overrides = {}
    if args.entropy_coef is not None:
        reward_overrides["entropy_coef"] = args.entropy_coef
    if args.act_clear_bonus is not None:
        reward_overrides["act_clear_bonus"] = args.act_clear_bonus
    if args.hp_shaping_coef is not None:
        reward_overrides["hp_shaping_coef"] = args.hp_shaping_coef
    config = PPOConfig(
        learning_rate=args.lr,
        episodes_per_iteration=args.episodes_per_iteration,
        minibatch_size=args.minibatch_size,
        update_epochs=args.update_epochs,
        target_kl=args.target_kl,
        hard_kl_stop=args.hard_kl_stop,
        floor_shaping=not args.terminal_only,
        **reward_overrides,
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
        "resolved_vocab_source": resolved_vocab_source,
        "vocab_report": vocab_report,
        "dev_seed_hash": split["development_seed_hash"],
    }, indent=2, default=str) + "\n", encoding="utf-8")

    if args.combat_backend in ("sim", "zhiyue"):
        # The simulator serves its own act1 catalog, so no real engine is needed.
        # It exposes a single act1 region, so there is no act variant to detect.
        with SimCombatBackend() as probe:
            catalog = probe.list_models("encounter")
        act_variant = None
    else:
        with make_client() as probe:
            catalog = probe.list_models("encounter")
            act_variant = None if args.all_act_regions else detect_act_variant(probe, catalog)
    on_policy_buffer_path = run_dir / "on_policy_boss_loadouts.json"
    on_policy_boss_loadouts: list[Loadout] = []
    if args.on_policy_boss_replay and on_policy_buffer_path.exists():
        saved = json.loads(on_policy_buffer_path.read_text(encoding="utf-8"))
        on_policy_boss_loadouts = [
            _loadout_from_dict(row) for row in saved.get("loadouts", [])
        ][-args.boss_replay_buffer_size:]

    boss_loadouts: tuple[Loadout, ...] = ()
    if (args.on_policy_boss_replay
            and len(on_policy_boss_loadouts) >= args.boss_replay_min_loadouts):
        # On resume, expose the accumulated native snapshots as a first-class
        # curriculum rung.  This allows focused boss pretraining before going
        # back to Act 1, instead of only mixing a small replay slice into runs.
        boss_loadouts = tuple(on_policy_boss_loadouts)
    elif not args.on_policy_boss_replay and LOADOUTS_PATH.exists():
        harvested = json.loads(LOADOUTS_PATH.read_text(encoding="utf-8"))
        boss_loadouts = tuple(_loadout_from_dict(row) for row in harvested["loadouts"])
    stages = ironclad_stages(catalog, boss_loadouts, act_variant=act_variant)
    boss_stage = next((s for s in stages if s.name == "boss_combat"), None)
    boss_encounters = tuple(sorted(
        str(row["id"]) for row in catalog
        if row["act"] == 1 and row["category"] == "boss"
        and (act_variant is None or str(row.get("act_id")) == act_variant)
    ))
    print(json.dumps({
        "act_variant": act_variant,
        "combat_backend": args.combat_backend,
        "eval_backend": args.eval_backend,
        "encounters": {s.name: len(s.encounters) for s in stages if s.is_combat},
        "boss_mix": args.boss_mix,
        "boss_replay_source": (
            "on_policy" if args.on_policy_boss_replay else "legacy_static"
        ),
        "on_policy_boss_loadouts": len(on_policy_boss_loadouts),
    }))
    clients = [make_combat_backend(args.combat_backend) for _ in range(args.workers)]
    # Evaluate on the ground-truth engine when asked, even while sampling from the
    # simulator; otherwise dev evals reuse the training backend.
    if args.eval_backend == "real" and args.combat_backend != "real":
        eval_clients = [make_client() for _ in range(args.workers)]
    else:
        eval_clients = clients
    stage_index = 0
    iteration_start = 0
    seed_cursor = 0
    resumed_from_step: int | None = None
    last_dev_win_rate: float | None = None

    if args.resume:
        payload = load_checkpoint(args.resume, model, optimizer)
        if payload.get("migrated_keys"):
            print(json.dumps({"resume_migrated": payload["migrated_keys"],
                              "optimizer_reset": bool(payload.get("optimizer_skipped"))}))
        saved_stage = payload["config"].get("stage")
        stage_names = [stage.name for stage in stages]
        stage_index = (
            stage_names.index(saved_stage) if saved_stage in stage_names
            else int(payload["config"].get("stage_index", 0))
        )
        resumed_from_step = int(payload["step"])
        iteration_start = resumed_from_step + 1
        seed_cursor = int(payload["config"].get("seed_cursor", 0))
        last_dev_win_rate = payload["config"].get("dev_win_rate")
    if args.stage:
        stage_index = [s.name for s in stages].index(args.stage)
    max_stage_index = (
        [s.name for s in stages].index(args.max_stage) if args.max_stage else len(stages) - 1
    )
    if args.combat_backend == "sim":
        # SimCombatBackend is combat-only: run stages (act1/full_a0) reach the
        # engine through client.reset(RunConfig), which it does not implement.
        # (The 'zhiyue' backend wraps RunManager and does serve run stages.)
        # Refuse to start on a run stage and never let advancement cross into one.
        combat_indices = [i for i, s in enumerate(stages) if s.is_combat]
        last_combat_index = max(combat_indices)
        if not stages[stage_index].is_combat:
            raise SystemExit(
                f"--combat-backend sim cannot run non-combat stage "
                f"{stages[stage_index].name!r}; pick a combat stage "
                f"(one of {[stages[i].name for i in combat_indices]})"
            )
        if max_stage_index > last_combat_index:
            max_stage_index = last_combat_index
    if resumed_from_step is not None:
        # Append-only branch marker: raw history/artifacts remain available for
        # audits, while dashboard readers can discard metrics produced after a
        # checkpoint that the model no longer contains.
        resume_marker = {
            "event": "resume", "resume_from_iteration": resumed_from_step,
            "checkpoint": str(args.resume),
            "stage": stages[min(stage_index, max_stage_index)].name,
        }
        history_writer.append(resume_marker)
        print(json.dumps(resume_marker))

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
            active_boss_stage = boss_stage
            if (args.on_policy_boss_replay
                    and len(on_policy_boss_loadouts) >= args.boss_replay_min_loadouts):
                active_boss_stage = CurriculumStage(
                    "boss_combat", boss_encounters,
                    loadouts=tuple(on_policy_boss_loadouts),
                )
            boss_seeds: list[str] = []
            if active_boss_stage is not None and not stage.is_combat and args.boss_mix > 0:
                boss_seeds, seeds = boss_replay_split(seeds, args.boss_mix)
            records = collect_iteration(
                clients, stage, seeds, agent, config, iteration=iteration, live=live,
            )
            boss_records = []
            if boss_seeds:
                boss_records = collect_iteration(
                    clients, active_boss_stage, boss_seeds, agent, config,
                    iteration=iteration, live=live,
                )
            new_boss_loadouts = 0
            if args.on_policy_boss_replay and not stage.is_combat:
                new_boss_loadouts = update_on_policy_boss_buffer(
                    on_policy_boss_loadouts, records,
                    args.boss_replay_buffer_size,
                )
                save_on_policy_boss_buffer(
                    on_policy_buffer_path, on_policy_boss_loadouts,
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
            visibility = visibility_audit(trained, vocab)
            sim_vocab_gaps = bool(
                resolved_vocab_source == "sim"
                and (visibility["unknown_entities"]
                     or visibility["unknown_dynamic_entities"])
            )
            if ((visibility["violations"] or sim_vocab_gaps)
                    and not args.allow_visibility_violations):
                # Persist the exact failing batch even when periodic train
                # recording is disabled, so vocabulary gaps are reproducible.
                episode_writer.write_many(
                    trained, iteration=iteration, stage=stage.name,
                    split="visibility_failure", character="Ironclad",
                )
                print(json.dumps({"visibility_failure": visibility}, ensure_ascii=False),
                      file=sys.stderr, flush=True)
                raise SystemExit(
                    "visibility contract failed: "
                    + ("; ".join(visibility["violations"])
                       or "sim-native vocabulary contains unknown entities")
                )
            # Reward inversion must stop before the first gradient update. Logging
            # it after PPO already let one poisoned batch change the policy.
            health = reward_health(records, config.gamma)
            if health["inverted"]:
                raise SystemExit(
                    "reward contract failed before PPO: losing returns "
                    f"{health['loss_return']} but winning returns {health['win_return']}"
                )
            live.set_all("updating", iteration=iteration, stage=stage.name, split="train")
            epoch_stats = []
            for _ in range(config.update_epochs):
                stats = ppo_update_epoch(model, optimizer, trained, vocab, config)
                epoch_stats.append(stats)
                if config.hard_kl_stop is not None and stats["approx_kl"] >= config.hard_kl_stop:
                    break
                if config.target_kl is not None and stats["approx_kl"] >= config.target_kl:
                    break
            stats = {
                key: sum(epoch[key] for epoch in epoch_stats) / len(epoch_stats)
                for key in epoch_stats[0]
            }
            stats["update_epochs_ran"] = len(epoch_stats)
            wins = sum(r.outcome is True for r in records)
            steps_total = sum(len(r.steps) for r in trained)
            row = {
                "iteration": iteration, "stage": stage.name,
                "train_win_rate": wins / max(len(records), 1),
                "episodes": len(records), "errors": errors,
                "steps": steps_total,
                "avg_floor": round(sum(r.final_floor for r in records) / max(len(records), 1), 2),
                "visibility": visibility,
                **stats,
                "seconds": round(time.perf_counter() - start, 1),
            }
            if errors:
                row["episode_errors"] = sorted({
                    str(record.error) for record in trained if record.error
                })[:20]
                row["main_errors"] = sum(record.error is not None for record in records)
                row["boss_replay_errors"] = sum(
                    record.error is not None for record in boss_records
                )
            if args.on_policy_boss_replay:
                row["on_policy_boss_loadouts"] = len(on_policy_boss_loadouts)
                row["new_boss_loadouts"] = new_boss_loadouts
                row["boss_replay_ready"] = (
                    len(on_policy_boss_loadouts) >= args.boss_replay_min_loadouts
                )
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
            row["reward_health"] = health
            row["action_mix"] = action_mix(records)
            # Engine no-op loops (e.g. buy_potion into a full belt) that the
            # circuit breaker caught and suppressed this iteration. Should trend to
            # a small, stable count; a spike means a new swallowed-exception no-op.
            row["noop_steps"] = sum(r.noop_steps for r in records)
            if not stage.is_combat:
                row["depth"] = depth_profile(records, acts=(stage.max_act or 3))
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
            for key in (
                "train_win_rate", "avg_floor", "loss", "policy_loss", "value_loss",
                "entropy", "approx_kl", "clip_fraction", "grad_norm",
                "grad_norm_clipped", "explained_variance", "update_epochs_ran",
            ):
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
                    eval_clients, stage, dev_seeds[: args.eval_episodes], agent, config,
                    iteration=iteration, live=live,
                )
                episode_writer.write_many(
                    evaluation_records, iteration=iteration, stage=stage.name,
                    split="dev", character="Ironclad",
                )
                logger.scalar(f"{stage.name}/dev_win_rate", evaluation["win_rate"], iteration)
                last_dev_win_rate = float(evaluation["win_rate"])
                dev_row = {"iteration": iteration, "dev": evaluation, "stage": stage.name}
                if not stage.is_combat:
                    dev_row["dev"] = {**evaluation, **depth_profile(evaluation_records, acts=(stage.max_act or 3))}
                    for key in ("reached_boss_rate", "boss_conversion"):
                        if key in dev_row["dev"]:
                            logger.scalar(f"{stage.name}/dev_{key}",
                                          float(dev_row["dev"][key]), iteration)
                # Dev rows used to go to stdout only, never to history.jsonl, so every
                # dashboard reading a run with a history file showed an empty validation
                # series and a blank "best dev win rate" tile.
                history_writer.append(dev_row)
                print(json.dumps(dev_row))
                resumed_stage_index = stage_index_after_evaluation(
                    stage_index, max_stage_index, stage.name, evaluation["win_rate"],
                )
                advanced = resumed_stage_index != stage_index
                stage_index = resumed_stage_index
                save_checkpoint(
                    run_dir / f"ckpt_{iteration:05d}.pt", model, optimizer,
                    step=iteration,
                    config={"stage_index": stage_index, "seed_cursor": seed_cursor,
                            "stage": stages[stage_index].name,
                            "evaluated_stage": stage.name,
                            "dev_win_rate": evaluation["win_rate"],
                            "init_seed": args.init_seed, "terminal_only": args.terminal_only},
                )
                if advanced:
                    print(json.dumps({"advanced_to": stages[stage_index].name, "iteration": iteration}))
            # A compact rolling recovery point protects every successful PPO
            # update. Milestone checkpoints remain every eval interval for
            # comparison/final selection; watchdog resumes whichever is newer.
            save_checkpoint(
                run_dir / "resume.pt", model, optimizer, step=iteration,
                config={"stage_index": stage_index, "seed_cursor": seed_cursor,
                        "stage": stages[min(stage_index, max_stage_index)].name,
                        "dev_win_rate": last_dev_win_rate,
                        "init_seed": args.init_seed,
                        "terminal_only": args.terminal_only},
            )
    finally:
        if isinstance(agent, BatchedAgent):
            agent.close()
        for client in clients:
            client.close()
        if eval_clients is not clients:
            for client in eval_clients:
                client.close()
        logger.close()
        live.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
