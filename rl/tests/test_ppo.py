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
from sts2rl.ppo import EpisodeRecord, PPOConfig, StoredStep, finalize_episode, ppo_update_epoch, run_episode
from sts2rl.protocol import ActionCandidate, DecisionState, StepResult

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


def test_run_episode_emits_compact_live_events_without_affecting_record():
    from sts2rl.agent import AgentStep

    action = ActionCandidate("play_card", {"card_index": 0, "target_index": 0})
    combat = DecisionState({
        "decision": "combat_play", "context": {"act": 1, "floor": 1, "round": 2},
        "player": {"hp": 70, "max_hp": 80, "energy": 3},
        "hand": [{"index": 0, "name": "Bash"}],
        "enemies": [{"index": 0, "name": "Jaw Worm"}],
    }, (action,))
    reward = DecisionState({"decision": "card_reward", "context": {"act": 1, "floor": 1}}, ())

    class Client:
        def reset_combat(self, _config): return combat
        def step(self, _action): return StepResult(reward, terminated=False)

    class Agent:
        def act(self, *_args, **_kwargs): return AgentStep(0, -0.2, 0.4, None)

    events = []
    record = run_episode(
        Client(), CurriculumStage("normal_combat", ("W1",)), "seed-live",
        Agent(), PPOConfig(), live_callback=events.append,
    )
    assert record.outcome is True and len(record.steps) == 1
    assert [event["type"] for event in events] == ["episode_start", "action", "episode_end"]
    assert events[1]["selected_label"] == "Bash"
    assert events[1]["action"] == action.command()
    assert events[1]["floor"] == 1.0 and events[1]["round"] == 2


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


# Act 1 ships two disjoint regions; a run only ever visits one (300/300 sampled
# A0 Ironclad runs start in Overgrowth). Training both spends ~48% of every
# combat stage on monsters the agent will never meet.
REGION_CATALOG = [
    {"id": "OW1", "act": 1, "category": "weak", "act_id": "OVERGROWTH"},
    {"id": "OR1", "act": 1, "category": "regular", "act_id": "OVERGROWTH"},
    {"id": "OE1", "act": 1, "category": "elite", "act_id": "OVERGROWTH"},
    {"id": "OB1", "act": 1, "category": "boss", "act_id": "OVERGROWTH"},
    {"id": "UW1", "act": 1, "category": "weak", "act_id": "UNDERDOCKS"},
    {"id": "UR1", "act": 1, "category": "regular", "act_id": "UNDERDOCKS"},
    {"id": "UE1", "act": 1, "category": "elite", "act_id": "UNDERDOCKS"},
    {"id": "UB1", "act": 1, "category": "boss", "act_id": "UNDERDOCKS"},
]


def test_act_variant_restricts_every_combat_pool_to_the_visited_region():
    from sts2rl.curriculum import Loadout, act_variant_of

    loadouts = (Loadout(50, 80, ("STRIKE_IRONCLAD",), (), ()),)
    stages = {
        s.name: s for s in ironclad_stages(REGION_CATALOG, loadouts, act_variant="OVERGROWTH")
    }
    assert set(stages["normal_combat"].encounters) == {"OW1", "OR1"}
    assert set(stages["mixed_combat"].encounters) == {"OW1", "OR1", "OE1"}
    assert set(stages["boss_combat"].encounters) == {"OB1"}
    assert act_variant_of(REGION_CATALOG, "UB1") == "UNDERDOCKS"

    # unfiltered keeps both regions (the pre-fix behaviour)
    both = {s.name: s for s in ironclad_stages(REGION_CATALOG, loadouts)}
    assert set(both["boss_combat"].encounters) == {"OB1", "UB1"}


def test_unknown_act_variant_fails_closed_instead_of_training_half_a_curriculum():
    with pytest.raises(ValueError):
        ironclad_stages(REGION_CATALOG, act_variant="NO_SUCH_REGION")


def test_boss_replay_split_holds_out_a_slice_for_the_boss_stage():
    from sts2rl.curriculum import boss_replay_split

    seeds = [f"s{i}" for i in range(48)]
    boss, main = boss_replay_split(seeds, 0.15)
    assert len(boss) == 7 and len(main) == 41
    assert boss + main == seeds          # every seed is used exactly once
    assert not set(boss) & set(main)     # and never trained twice
    assert boss_replay_split(seeds, 0.0) == ([], seeds)
    with pytest.raises(ValueError):
        boss_replay_split(seeds, 1.0)


# The reward function used to teach the policy to walk to the boss and die.
# Potential-based shaping is policy-invariant only when EVERY terminal state has
# potential zero. The engine drops `floor` from the state that follows an act
# completion, so a WIN already had Phi = 0 — but a death lands on `game_over`,
# which still carries `floor`, leaving Phi = 0.2*floor. Measured on the real
# engine with v5's ckpt_00529: mean discounted return was +1.76 for a deep death
# and only +0.65 for a win, and all nine deep deaths outscored both wins.
def _run_stage():
    return CurriculumStage("act1", max_act=1)


def _scripted_run(final: DecisionState, floors=(1, 8, 17)):
    """A run that climbs `floors`, then lands on `final` (a win or a game_over)."""
    from sts2rl.agent import AgentStep

    action = ActionCandidate("select_map_node", {"col": 0, "row": 0})
    states = [
        DecisionState({"decision": "map_select", "context": {"act": 1, "floor": f}},
                      (action,))
        for f in floors
    ]

    class Client:
        def __init__(self):
            self.i = 0

        def reset(self, _config):
            return states[0]

        def step(self, _action):
            self.i += 1
            nxt = states[self.i] if self.i < len(states) else final
            return StepResult(nxt, terminated=False)

    class Agent:
        def act(self, *_args, **_kwargs):
            return AgentStep(0, -0.2, 0.4, None)

    return run_episode(Client(), _run_stage(), "seed-x", Agent(), PPOConfig())


def _discounted(record, gamma=PPOConfig().gamma):
    return sum((gamma ** i) * s.reward for i, s in enumerate(record.steps))


def test_winning_an_act_out_returns_dying_deep():
    # a win: the act advances, and the engine emits no floor on that state
    win = DecisionState({"decision": "map_select", "context": {"act": 2}}, ())
    # a death at the same depth: game_over still carries the floor
    death = DecisionState(
        {"decision": "game_over", "victory": False, "act": 1, "floor": 17}, ()
    )
    won = _scripted_run(win)
    died = _scripted_run(death)

    assert won.outcome is True and died.outcome is False
    assert _discounted(won) > _discounted(died), (
        f"dying ({_discounted(died):+.2f}) must not out-return winning "
        f"({_discounted(won):+.2f})"
    )


def test_a_win_is_recorded_at_the_deepest_floor_not_the_terminal_floor():
    # The act-2 state has no floor; recording it as the final floor logged every
    # win as floor 0 and dragged avg_floor down.
    win = DecisionState({"decision": "map_select", "context": {"act": 2}}, ())
    record = _scripted_run(win, floors=(1, 8, 17))
    assert record.outcome is True
    assert record.final_floor == 17
