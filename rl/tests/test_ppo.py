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

    # On-policy data: old_logp comes from the current model, action 0 is
    # always advantaged. One PPO epoch must move probability toward it.
    on_policy = agent.act(state, candidates, greedy=True)
    old_logp = agent.act(state, candidates).logp if on_policy.index == 0 else on_policy.logp
    records = []
    for _ in range(8):
        record = EpisodeRecord("s", [
            StoredStep(state, candidates, 0, old_logp, 0.0, 1.0, None) for _ in range(4)
        ], True, truncated=False)
        finalize_episode(record, PPOConfig())
        records.append(record)
    before = probability_of_action_zero()
    stats = ppo_update_epoch(
        model, optimizer, records, vocab, PPOConfig(minibatch_size=16, entropy_coef=0.0),
    )
    after = probability_of_action_zero()
    assert after > before
    assert all(torch.isfinite(torch.tensor(v)) for v in stats.values())
