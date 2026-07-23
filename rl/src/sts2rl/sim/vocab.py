"""Deterministic simulator vocabulary discovery for sim-only training."""

from __future__ import annotations

from collections import Counter
import random
from typing import Iterator, Mapping

from sts2rl.engine import CombatConfig, RunConfig
from sts2rl.entities import EntityVocab, extend_vocab_entries

from .run_backend import ZhiyueRunBackend


def _exploration_action(state, rng: random.Random):
    """Choose a legal action while favouring progress through combats."""
    candidates = list(state.candidates)
    if state.phase == "combat_play":
        non_end = [candidate for candidate in candidates if candidate.action != "end_turn"]
        targeted = [
            candidate for candidate in non_end if "target_index" in candidate.args
        ]
        return rng.choice(targeted or non_end or candidates)
    return rng.choice(candidates)


def _catalog_states(backend: ZhiyueRunBackend) -> Iterator[Mapping[str, object]]:
    """Project simulator registries through the same ids as live observations."""
    from sts2_env.core.enums import PowerId
    from sts2_env.potions.base import all_potion_models
    from sts2_env.relics.base import RelicId
    from sts2_env.run.rest_site import RestSiteOption

    combat = backend._combat  # noqa: SLF001 - adapter-owned projection helpers
    sid = combat._stable_id  # noqa: SLF001
    cards = [sid("card", card_id.name) for card_id in combat._CardId]  # noqa: SLF001
    powers = [sid("power", power_id.name) for power_id in PowerId]
    relics = [sid("relic", relic_id.name) for relic_id in RelicId]
    potions = [
        sid("potion", combat._upper_snake(model.potion_id))  # noqa: SLF001
        for model in all_potion_models()
    ]

    # Keep discovery states comfortably below runtime entity caps.  Reward
    # screens deliberately include relic/potion ids under the card entity kind,
    # matching the Phase-1 adapter's unified reward representation.
    chunk = 128
    for start in range(0, max(len(cards), len(powers), len(relics), len(potions)), chunk):
        yield {
            "type": "decision",
            "decision": "combat_play",
            "hand": [
                {"id": value, "index": index}
                for index, value in enumerate(cards[start:start + chunk])
            ],
            "player_powers": [
                {"id": value} for value in powers[start:start + chunk]
            ],
            "player": {
                "relics": [{"id": value} for value in relics[start:start + chunk]],
                "potions": [
                    {"id": value, "index": index}
                    for index, value in enumerate(potions[start:start + chunk])
                ],
            },
            "enemies": [],
        }
    reward_ids = cards + relics + potions
    for start in range(0, len(reward_ids), chunk):
        yield {
            "type": "decision",
            "decision": "card_reward",
            "cards": [
                {"id": value, "index": index}
                for index, value in enumerate(reward_ids[start:start + chunk])
            ],
            "player": {},
            "enemies": [],
        }

    def descendants(base):
        for child in base.__subclasses__():
            yield child
            yield from descendants(child)

    rest_option_ids = sorted({
        str(option_id)
        for cls in descendants(RestSiteOption)
        if (option_id := getattr(cls, "OPTION_ID", None))
    })
    yield {
        "type": "decision",
        "decision": "rest_site",
        "options": [
            {"id": option_id, "name": option_id, "index": index}
            for index, option_id in enumerate(rest_option_ids)
        ],
        "player": {},
        "enemies": [],
    }


def discover_sim_vocab(
    base: EntityVocab | None = None,
    *,
    run_rollouts: int = 64,
    combat_sweep: bool = True,
    seed: int = 0,
    max_steps: int = 4_000,
) -> tuple[EntityVocab, dict[str, object]]:
    """Discover ids from deterministic simulator states and append them to base.

    Full-run rollouts use high HP so every sample reaches the Act 1 boss and
    boss-relic screen.  The optional encounter sweep covers monster identities
    and powers that a finite set of generated maps may not visit.  No simulator
    ids are translated by hand: :meth:`EntityVocab.from_states` sees the exact
    protocol states that training will consume.
    """
    if run_rollouts < 0:
        raise ValueError("run_rollouts must be non-negative")
    if max_steps < 1:
        raise ValueError("max_steps must be positive")

    rng = random.Random(seed)
    phase_counts: Counter[str] = Counter()
    state_count = 0
    completed_runs = 0
    swept_combats = 0

    def states() -> Iterator[Mapping[str, object]]:
        nonlocal state_count, completed_runs, swept_combats
        with ZhiyueRunBackend() as backend:
            for raw in _catalog_states(backend):
                phase = str(raw["decision"])
                phase_counts[phase] += 1
                state_count += 1
                yield raw
            if combat_sweep:
                encounters = [
                    str(row["id"]) for row in backend.list_models("encounter")
                    if int(row.get("act", 0)) == 1
                ]
                for encounter in encounters:
                    state = backend.reset_combat(CombatConfig(
                        character="Ironclad",
                        seed=f"sim-vocab-combat:{seed}:{encounter}",
                        encounter=encounter,
                        hp=9_999,
                        max_hp=9_999,
                    ))
                    for _ in range(max_steps):
                        phase_counts[state.phase] += 1
                        state_count += 1
                        yield state.raw
                        if state.phase == "game_over":
                            swept_combats += 1
                            break
                        state = backend.step(_exploration_action(state, rng)).state

            for run_index in range(run_rollouts):
                state = backend.reset(RunConfig(
                    "Ironclad", f"sim-vocab-run:{seed}:{run_index}",
                ))
                # Vocabulary discovery is about reachable identities, not game
                # balance.  High HP makes the boss/relic/act-transition tail a
                # guaranteed part of the preflight corpus.
                manager = backend._mgr  # noqa: SLF001 - simulator audit hook
                manager.run_state.player.max_hp = 9_999
                manager.run_state.player.current_hp = 9_999
                state = backend._decision_state()  # noqa: SLF001
                for _ in range(max_steps):
                    phase_counts[state.phase] += 1
                    state_count += 1
                    yield state.raw
                    context = state.raw.get("context") or {}
                    if state.phase == "game_over" or int(context.get("act", 1)) > 1:
                        completed_runs += 1
                        break
                    state = backend.step(_exploration_action(state, rng)).state

    discovered = EntityVocab.from_states(states())
    if base is None:
        vocab = discovered
        added = vocab.size - 1
    else:
        merged = extend_vocab_entries(
            base.entries,
            {kind: rows.keys() for kind, rows in discovered.entries.items()},
        )
        vocab = EntityVocab(merged)
        added = vocab.size - base.size
    return vocab, {
        "source": "sim_discovery",
        "run_rollouts": run_rollouts,
        "combat_sweep": combat_sweep,
        "states": state_count,
        "completed_runs": completed_runs,
        "swept_combats": swept_combats,
        "discovered_ids": discovered.size - 1,
        "added_ids": added,
        "phase_counts": dict(sorted(phase_counts.items())),
    }
