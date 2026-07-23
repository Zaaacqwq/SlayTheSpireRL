"""Generate the strict Act 1 P2 parity report against the real engine.

The audit uses the engine's read-only pile oracle at every combat decision, so
mixed/upgraded decks and generated cards remain ordered without altering real
gameplay.  Initial enemy HP is the one explicitly locked residual RNG stream.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
import json
from pathlib import Path
import shutil
import sys
from typing import Any, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[3]
RL_SRC = REPO_ROOT / "rl" / "src"
if str(RL_SRC) not in sys.path:
    sys.path.insert(0, str(RL_SRC))

from sts2rl.engine import CombatConfig, EngineClient

from .diff_harness import (
    DEFAULT_CLI_ROOT,
    DEFAULT_DLL,
    DEFAULT_UPSTREAM_ROOT,
    ZhiyueCombatDriver,
    first_mismatch,
    run_diff,
    semantic_projection,
    sync_oracle_piles,
)


DEFAULT_OUTPUT = Path(__file__).with_name("sim_parity_report.json")

LOADOUTS: tuple[dict[str, Any], ...] = (
    {
        "name": "initial_homogeneous",
        "deck": ("STRIKE_IRONCLAD",) * 10,
        "relics": (),
        "potions": (),
    },
    {
        "name": "starter_mixed",
        "deck": (
            ("STRIKE_IRONCLAD",) * 5
            + ("DEFEND_IRONCLAD",) * 4
            + ("BASH",)
        ),
        "relics": (),
        "potions": (),
    },
    {
        "name": "upgraded_mixed",
        "deck": (
            ("STRIKE_IRONCLAD+",) * 4
            + ("DEFEND_IRONCLAD+",) * 4
            + ("BASH+", "IRON_WAVE+")
        ),
        "relics": (),
        "potions": (),
    },
    {
        "name": "common_cards_relics",
        "deck": (
            "STRIKE_IRONCLAD", "STRIKE_IRONCLAD", "STRIKE_IRONCLAD",
            "DEFEND_IRONCLAD", "DEFEND_IRONCLAD", "DEFEND_IRONCLAD",
            "BASH", "ANGER", "POMMEL_STRIKE", "SHRUG_IT_OFF",
        ),
        "relics": ("BURNING_BLOOD", "ANCHOR"),
        "potions": (),
    },
    {
        "name": "common_cards_relics_potions",
        "deck": (
            "STRIKE_IRONCLAD", "STRIKE_IRONCLAD", "DEFEND_IRONCLAD",
            "DEFEND_IRONCLAD", "BASH+", "ANGER", "IRON_WAVE",
            "POMMEL_STRIKE", "SHRUG_IT_OFF", "TWIN_STRIKE",
        ),
        "relics": ("BURNING_BLOOD", "ANCHOR"),
        "potions": ("BLOCK_POTION", "FIRE_POTION"),
    },
)


def _enemy_component(projection: Mapping[str, Any], key: str) -> list[Any]:
    return [enemy[key] for enemy in projection["enemies"]]


def _scenario_result(
    real_raw: Mapping[str, Any],
    sim_raw: Mapping[str, Any],
) -> dict[str, Any]:
    real = semantic_projection(real_raw)
    sim = semantic_projection(sim_raw)
    real_ids = _enemy_component(real, "id")
    sim_ids = _enemy_component(sim, "id")
    composition_match = real_ids == sim_ids
    mismatch = first_mismatch(real_raw, sim_raw)
    checks = {
        "composition": composition_match,
        "hp": composition_match and _enemy_component(real, "hp") == _enemy_component(sim, "hp"),
        "block": composition_match and _enemy_component(real, "block") == _enemy_component(sim, "block"),
        "powers": composition_match and _enemy_component(real, "powers") == _enemy_component(sim, "powers"),
        "intents": composition_match and _enemy_component(real, "intents") == _enemy_component(sim, "intents"),
        "player": real["player"] == sim["player"],
        "hand": real["hand"] == sim["hand"],
        "pile_counts": (
            real["draw_pile_count"], real["discard_pile_count"]
        ) == (
            sim["draw_pile_count"], sim["discard_pile_count"]
        ),
    }
    # Initial composition, HP, and intent randomness are the explicit residual
    # RNG classes allowed by plan section 6.1.  State mechanics are only judged
    # when the same enemy composition exists on both sides.
    core_match = all(checks[key] for key in ("player", "hand", "pile_counts"))
    if composition_match:
        core_match = core_match and checks["block"] and checks["powers"]
    return {
        "exact": mismatch is None,
        "first_mismatch": asdict(mismatch) if mismatch is not None else None,
        "checks": checks,
        "core_match": core_match,
        "real_enemy_ids": real_ids,
        "sim_enemy_ids": sim_ids,
        "real_enemy_hp": _enemy_component(real, "hp"),
        "sim_enemy_hp": _enemy_component(sim, "hp"),
    }


def run_initial_audit(
    real: EngineClient,
    sim: ZhiyueCombatDriver,
    *,
    seeds: Sequence[str],
    step_max_steps: int = 0,
) -> dict[str, Any]:
    encounters = [
        model for model in real.list_models("encounter")
        if int(model.get("act", -1)) == 1
    ]
    check_counts: dict[str, Counter[str]] = {}
    first_mismatches: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()
    loadout_counts: Counter[str] = Counter()
    loadout_eligible: Counter[str] = Counter()
    loadout_completed: Counter[str] = Counter()
    category_completed: Counter[str] = Counter()
    examples: list[dict[str, Any]] = []
    exact = 0
    core_match = 0
    errors: list[dict[str, str]] = []
    step_scenarios = 0
    step_matched = 0
    step_terminated = 0
    compared_steps = 0
    step_failure_fields: Counter[str] = Counter()
    step_failure_examples: list[dict[str, Any]] = []
    step_errors: list[dict[str, str]] = []

    for seed_index, seed in enumerate(seeds):
        ascension = (0, 8, 9)[seed_index % 3]
        for encounter_index, encounter in enumerate(encounters):
            encounter_id = str(encounter["id"])
            category = str(encounter.get("category", "unknown"))
            category_counts[category] += 1
            loadout = LOADOUTS[(seed_index + encounter_index) % len(LOADOUTS)]
            loadout_name = str(loadout["name"])
            loadout_counts[loadout_name] += 1
            config = CombatConfig(
                character="IRONCLAD",
                seed=seed,
                encounter=encounter_id,
                ascension=ascension,
                hp=80,
                max_hp=80,
                gold=99,
                deck=tuple(loadout["deck"]),
                relics=tuple(loadout["relics"]),
                potions=tuple(loadout["potions"]),
            )
            try:
                real_state = real.reset_combat(config)
                sim_state = sim.reset_combat(config)
                sim_state, pile_mismatch = sync_oracle_piles(
                    real, sim, real_state, sim_state,
                )
                if pile_mismatch is not None:
                    raise RuntimeError(f"initial pile multiset mismatch: {pile_mismatch}")
                result = _scenario_result(real_state.raw, sim_state.raw)
            except Exception as exc:  # keep the full batch diagnostic
                errors.append({
                    "seed": seed,
                    "encounter": encounter_id,
                    "error": repr(exc),
                })
                continue

            if result["exact"]:
                exact += 1
            if result["core_match"]:
                core_match += 1
            mismatch = result["first_mismatch"]
            if mismatch is not None:
                first_mismatches[str(mismatch["field"])] += 1
            for name, matched in result["checks"].items():
                check_counts.setdefault(name, Counter())["match" if matched else "mismatch"] += 1
            if not result["exact"] and len(examples) < 24:
                examples.append({
                    "seed": seed,
                    "ascension": ascension,
                    "encounter": encounter_id,
                    "loadout": loadout_name,
                    **result,
                })
            step_eligible = (
                result["core_match"]
                and result["checks"]["composition"]
            )
            if step_max_steps > 0 and step_eligible:
                step_scenarios += 1
                loadout_eligible[loadout_name] += 1
                try:
                    step_run = run_diff(
                        real,
                        sim,
                        config,
                        max_steps=step_max_steps,
                        sync_initial_enemy_hp=True,
                        sync_enemy_intents=True,
                    )
                    compared_steps += step_run.compared_steps
                    if step_run.matched:
                        step_matched += 1
                    if step_run.terminated:
                        step_terminated += 1
                        if step_run.matched:
                            loadout_completed[loadout_name] += 1
                            category_completed[category] += 1
                    if step_run.failure is not None:
                        step_failure_fields[step_run.failure.mismatch.field] += 1
                        if len(step_failure_examples) < 24:
                            step_failure_examples.append({
                                "seed": seed,
                                "ascension": ascension,
                                "encounter": encounter_id,
                                "loadout": loadout_name,
                                "step": step_run.failure.step,
                                "action": step_run.failure.action,
                                "mismatch": asdict(step_run.failure.mismatch),
                            })
                except Exception as exc:
                    step_errors.append({
                        "seed": seed,
                        "encounter": encounter_id,
                        "error": repr(exc),
                    })

    total = len(seeds) * len(encounters)
    complete_coverage = (
        all(loadout_completed[name] > 0 for name in loadout_counts)
        and all(category_completed[name] > 0 for name in category_counts)
    )
    acceptance_met = (
        step_terminated >= 500
        and step_terminated == step_scenarios
        and step_matched == step_scenarios
        and not step_failure_fields
        and not step_errors
        and complete_coverage
    )
    return {
        "phase": "P2-strict-differential",
        "acceptance_met": acceptance_met,
        "scenario_count": total,
        "encounter_count": len(encounters),
        "seeds": list(seeds),
        "ascensions": [0, 8, 9],
        "loadout_ladder": [loadout["name"] for loadout in LOADOUTS],
        "loadout_scenarios": dict(sorted(loadout_counts.items())),
        "exact_scenarios": exact,
        "core_match_scenarios": core_match,
        "errors": errors,
        "category_scenarios": dict(sorted(category_counts.items())),
        "field_checks": {
            name: dict(counts) for name, counts in sorted(check_counts.items())
        },
        "first_mismatch_fields": dict(first_mismatches.most_common()),
        "step_audit": {
            "max_steps": step_max_steps,
            "eligible_rng_locked_scenarios": step_scenarios,
            "matched_to_boundary": step_matched,
            "terminated": step_terminated,
            "compared_steps": compared_steps,
            "failure_fields": dict(step_failure_fields.most_common()),
            "errors": step_errors,
            "failure_examples": step_failure_examples,
            "completed_by_loadout": dict(sorted(loadout_completed.items())),
            "eligible_by_loadout": dict(sorted(loadout_eligible.items())),
            "completed_by_category": dict(sorted(category_completed.items())),
            "coverage_complete": complete_coverage,
        },
        "rng_whitelist": {
            "initial_enemy_composition": "real encounter RNG is separate from zhiyue's combined setup/HP RNG",
            "initial_enemy_hp": "real HP uses RunState.Rng.Niche; zhiyue consumes the encounter RNG",
            "initial_enemy_intent": "locked by matching the oracle projection to an existing simulator move",
            "summoned_enemy_hp": "summoned creatures also use the real Niche stream, so later summon HP can differ",
            "later_enemy_intent": "locked only when an existing simulator move has the exact oracle type/damage projection",
            "generated_card_shuffle": "aligned at every decision through the read-only get_draw_pile oracle",
            "postcombat_reward_hp": "card_reward may auto-grant Strawberry; an equal max/current HP gain is removed only at the combat-to-reward boundary",
        },
        "blockers": [] if acceptance_met else [
            "P2 remains incomplete until step_audit records at least 500 terminated, mismatch-free combats with full category/loadout coverage.",
            "A monster intent with no exact implemented simulator move remains a hard mismatch.",
        ],
        "mismatch_examples": examples,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed-count", type=int, default=30)
    parser.add_argument(
        "--step-max-steps",
        type=int,
        default=160,
        help="maximum differential actions per eligible combat",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--upstream", type=Path, default=DEFAULT_UPSTREAM_ROOT)
    parser.add_argument("--cli-root", type=Path, default=DEFAULT_CLI_ROOT)
    parser.add_argument("--dll", type=Path, default=DEFAULT_DLL)
    args = parser.parse_args(argv)
    if args.seed_count < 1:
        parser.error("--seed-count must be positive")
    if args.step_max_steps < 0:
        parser.error("--step-max-steps cannot be negative")

    seeds = tuple(f"sim-p2-{index:03d}" for index in range(args.seed_count))
    sim = ZhiyueCombatDriver(args.upstream)
    dotnet = shutil.which("dotnet") or "dotnet"
    with EngineClient(
        [dotnet, str(args.dll.resolve())],
        cwd=args.cli_root.resolve(),
        timeout=30,
    ) as real:
        report = run_initial_audit(
            real,
            sim,
            seeds=seeds,
            step_max_steps=args.step_max_steps,
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        key: report[key]
        for key in (
            "phase", "acceptance_met", "scenario_count", "encounter_count",
            "exact_scenarios", "core_match_scenarios", "field_checks",
            "step_audit",
        )
    }, ensure_ascii=False, indent=2))
    return 0 if not report["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
