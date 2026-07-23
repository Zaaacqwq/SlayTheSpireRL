"""End-to-end: our entity model drives a full combat on the zhiyue backend.

Guards the RL-pipeline <-> sim seam that ``test_backend`` (backend-only round
trip) does not: the model must consume the sim's ``raw`` states without NaN,
every decision must expose legal candidates, the sampled index must be in range
and executable, and the dynamic ``EntityVocab`` must absorb the sim's ids with
zero mapping table (sim-only training). Also asserts the sim ``raw`` schema stays
within our known-field set so obs normalization raises no warnings.
"""

from __future__ import annotations

import math

import torch

from sts2rl.agent import PolicyAgent
from sts2rl.engine import CombatConfig
from sts2rl.entities import EntityVocab
from sts2rl.model import EntityRecurrentPolicy
from sts2rl.observation import normalize_state
from sts2rl.entities import encode_entity_batch
from sts2rl.sim import SimCombatBackend, discover_sim_vocab


def _config(**overrides) -> CombatConfig:
    values = {
        "character": "IRONCLAD",
        "seed": "model-integration",
        "encounter": "SHRINKER_BEETLE_WEAK",
        "hp": 80,
        "max_hp": 80,
    }
    values.update(overrides)
    return CombatConfig(**values)


def test_model_drives_sim_combat_to_termination() -> None:
    torch.manual_seed(0)
    vocab = EntityVocab({})  # empty: sim ids all resolve to UNK but still encode
    model = EntityRecurrentPolicy(vocab_size=vocab.size, hidden=32, heads=2, layers=1)
    agent = PolicyAgent(model, vocab)
    generator = torch.Generator().manual_seed(0)

    seen: list[dict] = []
    with SimCombatBackend() as backend:
        state = backend.reset_combat(_config())
        hidden = None
        steps = 0
        while state.phase != "game_over" and steps < 500:
            seen.append(dict(state.raw))
            assert state.candidates, f"no legal candidates at {state.phase!r}"

            # obs must normalize cleanly: no NaN/inf numerics, schema in-vocab.
            observation = normalize_state(state.raw)
            assert not observation.warnings, observation.warnings
            batch = encode_entity_batch([observation], vocab)
            assert torch.isfinite(batch["entity_numeric"]).all()
            assert torch.isfinite(torch.tensor(observation.global_features)).all()

            step = agent.act(state.raw, state.candidates, hidden, generator=generator)
            assert 0 <= step.index < len(state.candidates)
            assert math.isfinite(step.value) and math.isfinite(step.logp)

            result = backend.step(state.candidates[step.index])
            state = result.state
            hidden = step.hidden
            steps += 1

    assert state.phase == "game_over", "random policy never terminated the combat"

    # Dynamic vocab: sim-only training learns the sim's ids as fresh tokens with
    # no hand-authored mapping table.
    dynamic = EntityVocab.from_states(seen)
    assert dynamic.size > 1
    assert "CARD.STRIKE_IRONCLAD" in dynamic.entries.get("card", {})
    assert "MONSTER.SHRINKER_BEETLE" in dynamic.entries.get("enemy", {})


def test_sim_vocab_discovery_covers_catalog_only_reward_ids() -> None:
    vocab, report = discover_sim_vocab(EntityVocab({}), run_rollouts=0)
    assert report["swept_combats"] == 42
    assert "POTION.FAIRY_INA_BOTTLE" in vocab.entries["potion"]
    # Relics and potions are intentionally projected as card entities on the
    # unified reward screen, so both identities must be native vocabulary rows.
    assert "RELIC.WHETSTONE" in vocab.entries["card"]
    assert "POTION.LIQUID_MEMORIES" in vocab.entries["card"]
    assert "LIFT" in vocab.entries["option"]
