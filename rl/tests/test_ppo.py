from __future__ import annotations

import pytest

torch = pytest.importorskip("torch")

from sts2rl.curriculum import (
    CurriculumStage,
    IRONCLAD_STARTING_DECK,
    episode_config,
    ironclad_stages,
    sample_encounter,
)
from sts2rl.engine import CombatConfig, RunConfig
from sts2rl.entities import EntityVocab
from sts2rl.model import EntityRecurrentPolicy
from sts2rl.ppo import EpisodeRecord, PPOConfig, StoredStep, finalize_episode, ppo_update_epoch
from sts2rl.protocol import ActionCandidate

CATALOG = [
    {"id": "W1", "act": 1, "category": "weak"},
    {"id": "W2", "act": 1, "category": "weak"},
    {"id": "R1", "act": 1, "category": "regular"},
    {"id": "E1", "act": 1, "category": "elite"},
    {"id": "B1", "act": 1, "category": "boss"},
    {"id": "A2R", "act": 2, "category": "regular"},
]


def test_ironclad_stages_follow_the_roadmap_ladder():
    stages = ironclad_stages(CATALOG)
    assert [s.name for s in stages] == ["normal_combat", "mixed_combat", "act1", "full_a0"]
    assert set(stages[0].encounters) == {"W1", "W2", "R1"}
    assert set(stages[1].encounters) == {"W1", "W2", "R1", "E1"}
    assert stages[2].max_act == 1 and not stages[2].is_combat
    assert stages[3].encounters == () and stages[3].max_act is None


def test_encounter_sampling_is_deterministic_and_seed_sensitive():
    stage = ironclad_stages(CATALOG)[0]
    assert sample_encounter(stage, "seed-1") == sample_encounter(stage, "seed-1")
    picks = {sample_encounter(stage, f"seed-{i}") for i in range(50)}
    assert len(picks) > 1


def test_episode_config_selects_combat_or_run():
    stages = ironclad_stages(CATALOG)
    combat = episode_config(stages[0], "seed-1")
    assert isinstance(combat, CombatConfig)
    assert combat.deck == IRONCLAD_STARTING_DECK
    full = episode_config(stages[3], "seed-1")
    assert isinstance(full, RunConfig)


def _record(rewards, values, truncated=False):
    steps = [
        StoredStep({}, (ActionCandidate("end_turn", {}),), 0, -0.5, v, r, None)
        for r, v in zip(rewards, values)
    ]
    return EpisodeRecord("seed", steps, None, truncated)


def test_finalize_episode_terminal_and_truncated_differ():
    terminal = _record([0.0, 1.0], [0.2, 0.3])
    finalize_episode(terminal, PPOConfig())
    truncated = _record([0.0, 1.0], [0.2, 0.3], truncated=True)
    finalize_episode(truncated, PPOConfig())
    # the truncated episode bootstraps V(last) instead of treating it as death
    assert terminal.advantages != truncated.advantages
    assert len(terminal.returns) == 2


def test_ppo_update_epoch_raises_probability_of_advantaged_action():
    torch.manual_seed(0)
    vocab = EntityVocab({})
    model = EntityRecurrentPolicy(vocab_size=vocab.size, hidden=32, heads=2, layers=1)
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3)
    state = {"decision": "combat_play", "player": {"hp": 10, "max_hp": 10},
             "hand": [{"index": 0, "id": "CARD.X", "cost": 1}], "enemies": []}
    candidates = (ActionCandidate("play_card", {"card_index": 0}), ActionCandidate("end_turn", {}))

    from sts2rl.agent import PolicyAgent
    agent = PolicyAgent(model, vocab)

    def probability_of_action_zero() -> float:
        from sts2rl.ppo import encode_update_batch
        batch = encode_update_batch(
            [StoredStep(state, candidates, 0, 0.0, 0.0, 0.0, None)], vocab, model.hidden_size,
        )
        with torch.no_grad():
            logits, _, _ = model(
                batch["global"], batch["entities"], batch["candidates"], batch["mask"],
                hidden=batch["hidden"],
            )
        return float(torch.softmax(logits[0], -1)[0])

    # Contrastive on-policy data: choosing action 0 pays +1, action 1 pays -1.
    # Advantage normalization keeps the signs opposed, so one PPO epoch must
    # move probability toward action 0. (Single-action data would get mixed
    # signs from normalization; value_coef=0 keeps the shared trunk out of it.)
    from sts2rl.ppo import encode_update_batch
    probe = encode_update_batch(
        [StoredStep(state, candidates, 0, 0.0, 0.0, 0.0, None)], vocab, model.hidden_size,
    )
    with torch.no_grad():
        logits, _, _ = model(probe["global"], probe["entities"], probe["candidates"],
                             probe["mask"], candidate_slots=probe["slots"], hidden=probe["hidden"])
        log_probs = torch.log_softmax(logits[0], -1)
    records = []
    for index, reward in ((0, 1.0), (1, -1.0)):
        for _ in range(4):
            record = EpisodeRecord("s", [
                StoredStep(state, candidates, index, float(log_probs[index]), 0.0, reward, None)
            ], True, truncated=False)
            finalize_episode(record, PPOConfig())
            records.append(record)
    before = probability_of_action_zero()
    stats = ppo_update_epoch(
        model, optimizer, records, vocab,
        PPOConfig(minibatch_size=16, entropy_coef=0.0, value_coef=0.0),
    )
    after = probability_of_action_zero()
    assert after > before
    assert all(torch.isfinite(torch.tensor(v)) for v in stats.values())


def test_combat_starting_hp_is_deterministic_and_bounded():
    from sts2rl.curriculum import COMBAT_HP_RANGE, sample_starting_hp
    stage = ironclad_stages(CATALOG)[0]
    values = [sample_starting_hp(stage, f"seed-{i}") for i in range(200)]
    assert all(COMBAT_HP_RANGE[0] <= v <= COMBAT_HP_RANGE[1] for v in values)
    assert sample_starting_hp(stage, "seed-3") == sample_starting_hp(stage, "seed-3")
    assert len(set(values)) > 20
    config = episode_config(stage, "seed-3")
    assert config.hp == sample_starting_hp(stage, "seed-3")
    assert config.max_hp == 80


def test_boss_bridge_stage_uses_harvested_loadouts():
    from sts2rl.curriculum import Loadout, sample_loadout
    loadouts = (
        Loadout(40, 80, ("STRIKE_IRONCLAD",) * 6, ("BURNING_BLOOD",), ()),
        Loadout(55, 80, ("BASH",) * 5, (), ("BLOCK_POTION",)),
    )
    stages = ironclad_stages(CATALOG, loadouts)
    names = [s.name for s in stages]
    assert names == ["normal_combat", "mixed_combat", "boss_combat", "act1", "full_a0"]
    bridge = stages[2]
    assert set(bridge.encounters) == {"B1"}
    assert sample_loadout(bridge, "seed-1") == sample_loadout(bridge, "seed-1")
    config = episode_config(bridge, "seed-1")
    chosen = sample_loadout(bridge, "seed-1")
    assert config.deck == chosen.deck and config.hp == chosen.hp
    # without loadouts the ladder is unchanged
    assert [s.name for s in ironclad_stages(CATALOG)] == [
        "normal_combat", "mixed_combat", "act1", "full_a0"]
