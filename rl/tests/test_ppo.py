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
    assert [s.name for s in stages] == ["normal_combat", "mixed_combat", "act1", "acts_12", "full_a0"]
    assert set(stages[0].encounters) == {"W1", "W2", "R1"}
    assert set(stages[1].encounters) == {"W1", "W2", "R1", "E1"}
    assert stages[2].max_act == 1 and not stages[2].is_combat
    assert stages[3].max_act == 2 and not stages[3].is_combat
    assert stages[4].encounters == () and stages[4].max_act is None


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
    full = episode_config(stages[4], "seed-1")
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


def test_circuit_breaker_suppresses_engine_noop_buy_loop():
    # A full potion belt makes the engine swallow the failed buy and return a
    # byte-identical shop state, which used to lock a greedy policy into buying
    # forever until the 600-step truncation. The breaker must catch the no-op,
    # stop offering that action, and let the run leave the shop and finish.
    from sts2rl.agent import AgentStep

    buy = ActionCandidate("buy_potion", {"potion_index": 0})
    leave = ActionCandidate("leave_room", {})
    shop = DecisionState({
        "decision": "shop", "context": {"act": 1, "floor": 5},
        "player": {"hp": 50, "max_hp": 80, "gold": 999},
        "potions": [{"index": 0, "name": "Fire Potion", "affordable": True}],
    }, (buy, leave))
    win = DecisionState({"decision": "game_over", "victory": True, "act": 1, "floor": 5}, ())

    class Client:
        def reset(self, _config):
            return shop

        def step(self, action):
            if action.action == "buy_potion":
                return StepResult(shop, terminated=False)  # engine no-op: identical state
            return StepResult(win, terminated=True)

    class Agent:
        # Always prefers the first offered candidate — i.e. buy_potion while it is
        # still offered. Without the breaker this loops to max_episode_steps.
        def act(self, _raw, candidates, _hidden, **_kwargs):
            return AgentStep(0, -0.2, 0.4, None)

    record = run_episode(Client(), _run_stage(), "seed-shop", Agent(), PPOConfig())

    assert record.outcome is True and not record.truncated
    assert record.noop_steps == 1  # exactly one dead buy, then it was masked
    chosen = [s.candidates[s.index].action for s in record.steps]
    assert chosen == ["buy_potion", "leave_room"]
    # once proven inert the action is no longer even presented to the policy
    assert record.steps[-1].candidates == (leave,)


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


def test_ppo_update_epoch_stops_inside_epoch_at_hard_kl_limit():
    from sts2rl.ppo import encode_update_batch

    state = {"decision": "combat_play", "player": {"hp": 10, "max_hp": 10},
             "hand": [{"index": 0, "id": "CARD.X", "cost": 1}], "enemies": []}
    candidates = (ActionCandidate("play_card", {"card_index": 0}),
                  ActionCandidate("end_turn", {}))

    def build():
        torch.manual_seed(0)
        vocab = EntityVocab({})
        model = EntityRecurrentPolicy(vocab_size=vocab.size, hidden=32, heads=2, layers=1)
        optimizer = torch.optim.AdamW(model.parameters(), lr=1e-1)
        probe = encode_update_batch(
            [StoredStep(state, candidates, 0, 0.0, 0.0, 0.0, None)],
            vocab, model.hidden_size,
        )
        with torch.no_grad():
            logits, _, _ = model(
                probe["global"], probe["entities"], probe["candidates"], probe["mask"],
                candidate_slots=probe["slots"], hidden=probe["hidden"],
            )
            log_probs = torch.log_softmax(logits[0], -1)
        records = []
        for index, reward in ((0, 1.0), (1, -1.0)):
            for _ in range(4):
                record = EpisodeRecord("s", [StoredStep(
                    state, candidates, index, float(log_probs[index]), 0.0, reward, None,
                )], True, truncated=False)
                finalize_episode(record, PPOConfig())
                records.append(record)
        return vocab, model, optimizer, records

    common = dict(minibatch_size=1, entropy_coef=0.0, value_coef=0.0)
    vocab, model, optimizer, records = build()
    unguarded = ppo_update_epoch(
        model, optimizer, records, vocab, PPOConfig(hard_kl_stop=None, **common),
    )
    vocab, model, optimizer, records = build()
    guarded = ppo_update_epoch(
        model, optimizer, records, vocab, PPOConfig(hard_kl_stop=1e-9, **common),
    )
    assert unguarded["minibatches"] == 8.0
    assert guarded["minibatches"] == 1.0


def test_length_aware_batches_use_every_step_once_and_pack_similar_states():
    from sts2rl.ppo import _length_aware_batches

    candidates = (ActionCandidate("end_turn", {}),)
    steps = []
    for size in range(1, 9):
        state = {
            "decision": "combat_play", "player": {},
            "hand": [{"index": i, "id": f"CARD.{i}"} for i in range(size)],
        }
        steps.append(StoredStep(state, candidates, 0, 0.0, 0.0, 0.0, None))
    batches = _length_aware_batches(steps, 2, torch.Generator().manual_seed(7))
    assert sorted(index for batch in batches for index in batch) == list(range(8))
    assert all(max(batch) - min(batch) == 1 for batch in batches)


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
    assert names == ["normal_combat", "mixed_combat", "boss_combat", "act1", "acts_12", "full_a0"]
    bridge = stages[2]
    assert set(bridge.encounters) == {"B1"}
    assert sample_loadout(bridge, "seed-1") == sample_loadout(bridge, "seed-1")
    config = episode_config(bridge, "seed-1")
    chosen = sample_loadout(bridge, "seed-1")
    assert config.deck == chosen.deck and config.hp == chosen.hp
    # without loadouts the ladder is unchanged
    assert [s.name for s in ironclad_stages(CATALOG)] == [
        "normal_combat", "mixed_combat", "act1", "acts_12", "full_a0"]


def test_on_policy_boss_snapshot_preserves_upgrades_and_actual_encounter():
    from sts2rl.curriculum import boss_loadout_from_state

    state = {
        "decision": "combat_play", "round": 1,
        "context": {"room_type": "Boss", "boss": {"id": "B1"}},
        "player": {
            "hp": 31, "max_hp": 84,
            "deck": [
                {"id": "CARD.BASH", "upgraded": True},
                {"id": "CARD.STRIKE_IRONCLAD", "upgraded": False},
            ],
            "relics": [{"id": "RELIC.BURNING_BLOOD"}],
            "potions": [{"id": "POTION.BLOCK_POTION"}],
        },
    }
    loadout = boss_loadout_from_state(state)
    assert loadout is not None
    assert loadout.encounter == "B1"
    assert loadout.deck == ("BASH+", "STRIKE_IRONCLAD")

    stage = CurriculumStage("boss_combat", ("B1", "OTHER"), loadouts=(loadout,))
    config = episode_config(stage, "seed")
    assert config.encounter == "B1" and config.deck == loadout.deck


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


def _scripted_hp_run(final, hps, config, max_hp=80):
    """A run whose HP follows `hps` across the map, then lands on `final`."""
    from sts2rl.agent import AgentStep

    action = ActionCandidate("select_map_node", {"col": 0, "row": 0})
    states = [
        DecisionState({"decision": "map_select", "context": {"act": 1, "floor": f + 1},
                       "player": {"hp": hp, "max_hp": max_hp}}, (action,))
        for f, hp in enumerate(hps)
    ]

    class Client:
        def __init__(self): self.i = 0
        def reset(self, _config): return states[0]
        def step(self, _action):
            self.i += 1
            nxt = states[self.i] if self.i < len(states) else final
            return StepResult(nxt, terminated=False)

    class Agent:
        def act(self, *_args, **_kwargs): return AgentStep(0, -0.2, 0.4, None)

    return run_episode(Client(), _run_stage(), "seed-hp", Agent(), config)


def test_hp_shaping_is_policy_invariant_and_off_by_default():
    # Off by default: with coef 0 (PPOConfig default) the HP path is irrelevant —
    # two runs down the same floors with different HP produce identical rewards.
    death = DecisionState({"decision": "game_over", "victory": False, "act": 1, "floor": 17}, ())
    base = _scripted_hp_run(death, hps=(80, 56, 40), config=PPOConfig())
    base_other_hp = _scripted_hp_run(death, hps=(80, 72, 64), config=PPOConfig())
    assert [round(s.reward, 6) for s in base.steps] == [round(s.reward, 6) for s in base_other_hp.steps]

    # On: potential-based shaping telescopes over the whole episode to the constant
    # -coef * Phi(s0) (Phi forced to 0 at the terminal), independent of the HP path
    # between — the invariance that keeps it from changing the optimum. Isolate the
    # HP contribution as the discounted-return difference against coef=0.
    coef, gamma = 0.3, PPOConfig().gamma
    shaped = _scripted_hp_run(death, hps=(80, 56, 40), config=PPOConfig(hp_shaping_coef=coef))
    delta = _discounted(shaped) - _discounted(base)
    assert delta == pytest.approx(-coef * 1.0, abs=1e-6)  # Phi(s0) = 80/80 = 1.0

    # A different HP path from the same start (and same terminal) yields the same
    # total shaping contribution: proof it is path-independent, not a damage tax.
    shaped2 = _scripted_hp_run(death, hps=(80, 72, 64), config=PPOConfig(hp_shaping_coef=coef))
    base2 = _scripted_hp_run(death, hps=(80, 72, 64), config=PPOConfig())
    assert _discounted(shaped2) - _discounted(base2) == pytest.approx(-coef * 1.0, abs=1e-6)

    # Per-step sign: taking damage (HP drops) is a negative reward that step.
    step_hp_terms = [s.reward - b.reward for s, b in zip(shaped.steps, base.steps)]
    assert step_hp_terms[0] < 0  # 80 -> 56 across the first transition


def test_hp_potential_is_concave_pays_little_for_topping_off():
    # The reason the potential is g(x)=1-(1-x)^2, not linear: healing from low HP
    # (where death is) must pay far more than topping off near full, so HP shaping
    # stops bribing the wasteful rest heal that entrenched always-HEAL.
    death = DecisionState({"decision": "game_over", "victory": False, "act": 1, "floor": 17}, ())
    coef = 0.3

    def heal_term(hps):  # the HP-shaping reward on the (healing) first transition
        base = _scripted_hp_run(death, hps=hps, config=PPOConfig(), max_hp=100)
        shaped = _scripted_hp_run(death, hps=hps, config=PPOConfig(hp_shaping_coef=coef), max_hp=100)
        return shaped.steps[0].reward - base.steps[0].reward

    low = heal_term((40, 60, 60))    # recover from 40% -> 60%
    high = heal_term((80, 100, 100))  # top off 80% -> 100%
    assert low > 0 and high > 0       # healing is always rewarded
    assert low > 3 * high             # but low-HP recovery pays far more


def _bonus_per_step(states, config: PPOConfig) -> list[float]:
    """Per-step reward delta between `config` and the plain config on one scripted
    episode. Shaping is identical across both, so what is left is the bonus."""
    from sts2rl.agent import AgentStep

    death = DecisionState({"decision": "game_over", "victory": False, "act": 1, "floor": 9}, ())

    class Client:
        def __init__(self): self.i = 0
        def reset(self, _config): return states[0]
        def step(self, _action):
            self.i += 1
            return StepResult(states[self.i] if self.i < len(states) else death, terminated=False)

    class Agent:
        def act(self, *_args, **_kwargs): return AgentStep(0, -0.2, 0.4, None)

    off = run_episode(Client(), _run_stage(), "seed-sr", Agent(), PPOConfig())
    on = run_episode(Client(), _run_stage(), "seed-sr", Agent(), config)
    return [a.reward - b.reward for a, b in zip(on.steps, off.steps)]


def _shop(floor: int, deck_size: int, *candidates) -> DecisionState:
    return DecisionState({
        "decision": "shop", "context": {"act": 1, "floor": floor},
        "player": {"hp": 70, "max_hp": 80, "deck_size": deck_size},
    }, candidates)


def test_chose_option_maps_an_index_back_to_its_semantic_id():
    """Rest/event choices arrive as `choose_option` with a bare index, so anything
    that wants to name one (forced exploration's `option:SMITH`) has to resolve it
    through the state's options rather than trust the ordering."""
    from sts2rl.ppo import _chose_option

    rest = DecisionState({
        "decision": "rest_site", "context": {"act": 1, "floor": 6},
        "player": {"hp": 70, "max_hp": 80},
        "options": [{"index": 0, "option_id": "HEAL"}, {"index": 1, "option_id": "SMITH"}],
    }, ())

    assert not _chose_option(rest, ActionCandidate("choose_option", {"option_index": 0}), "SMITH")
    assert _chose_option(rest, ActionCandidate("choose_option", {"option_index": 1}), "SMITH")
    assert not _chose_option(rest, ActionCandidate("end_turn", {}), "SMITH")


def test_remove_bonus_pays_for_the_removal_not_for_opening_the_screen():
    """``remove_card`` only opens the removal screen: it charges nothing and
    returns a ``card_select``. That selection is player-opened, so the engine lets
    ``skip_select`` cancel it (RunSimulator.DoSkipSelect). Paying on the button
    would therefore teach the cheapest way to collect the bonus — open the screen,
    cancel, keep both the card and the 75 gold."""
    select = DecisionState({
        "decision": "card_select", "context": {"act": 1, "floor": 7},
        "player": {"hp": 70, "max_hp": 80, "deck_size": 18},
    }, (ActionCandidate("select_cards", {"indices": "3"}),
        ActionCandidate("skip_select", {})))

    completed = _bonus_per_step([
        _shop(7, 18, ActionCandidate("remove_card", {})),
        select,
        _shop(7, 17, ActionCandidate("leave_room", {})),  # the card is gone
    ], PPOConfig(remove_bonus=0.07))

    # Nothing on the button; the bonus lands on the step that shrank the deck.
    assert completed[0] == pytest.approx(0.0, abs=1e-6)
    assert completed[1] == pytest.approx(0.07, abs=1e-6)

    cancelled = _bonus_per_step([
        _shop(7, 18, ActionCandidate("remove_card", {})),
        select,
        _shop(7, 18, ActionCandidate("leave_room", {})),  # skip_select kept the card
    ], PPOConfig(remove_bonus=0.07))

    assert cancelled == [pytest.approx(0.0, abs=1e-6)] * len(cancelled)


def test_remove_bonus_ignores_screens_that_do_not_report_the_deck():
    """A map/event screen without a deck must not read as a deck of size zero, or
    every transition into one would pay the bonus."""
    from sts2rl.ppo import _removed_a_card

    with_deck = DecisionState({"player": {"deck_size": 18}}, ())
    without = DecisionState({"player": {"hp": 70}}, ())

    assert not _removed_a_card(with_deck, without)
    assert not _removed_a_card(without, with_deck)
    assert _removed_a_card(with_deck, DecisionState({"player": {"deck_size": 17}}, ()))
    # deck_size is absent on some payloads; the card list is the fallback
    assert _removed_a_card(with_deck, DecisionState({"player": {"deck": [{}] * 17}}, ()))


def test_smith_bonus_pays_for_the_upgrade_not_for_pressing_smith():
    """Choosing SMITH only opens the "which card" selection — `DoChooseOption`
    returns as soon as `_cardSelector.HasPending` ("so Smith card selection can
    pause"). A rest-site selection is player-opened, so `skip_select` cancels it,
    and a cancelled Smith is worse than Heal: the rest site is spent with neither
    the upgrade nor the heal. Paying on the button would train exactly that."""
    def deck(upgraded: int, total: int = 3):
        return [{"id": f"CARD.C{i}", "upgraded": i < upgraded} for i in range(total)]

    rest = DecisionState({
        "decision": "rest_site", "context": {"act": 1, "floor": 6, "room_type": "RestSite"},
        "player": {"hp": 70, "max_hp": 80, "deck": deck(0)},
        "options": [{"index": 0, "option_id": "SMITH"}, {"index": 1, "option_id": "HEAL"}],
    }, (ActionCandidate("choose_option", {"option_index": 0}),))
    select = DecisionState({
        "decision": "card_select", "context": {"act": 1, "floor": 6, "room_type": "RestSite"},
        "player": {"hp": 70, "max_hp": 80, "deck": deck(0)},
    }, (ActionCandidate("select_cards", {"indices": "0"}),))

    upgraded = _bonus_per_step([rest, select, DecisionState({
        "decision": "map_select", "context": {"act": 1, "floor": 7, "room_type": "Map"},
        "player": {"hp": 70, "max_hp": 80, "deck": deck(1)},
    }, (ActionCandidate("select_map_node", {"col": 0, "row": 0}),))],
        PPOConfig(smith_bonus=0.05))
    assert upgraded[0] == pytest.approx(0.0, abs=1e-6)   # the button pays nothing
    assert upgraded[1] == pytest.approx(0.05, abs=1e-6)  # the upgrade does

    cancelled = _bonus_per_step([rest, select, DecisionState({
        "decision": "map_select", "context": {"act": 1, "floor": 7, "room_type": "Map"},
        "player": {"hp": 70, "max_hp": 80, "deck": deck(0)},
    }, (ActionCandidate("select_map_node", {"col": 0, "row": 0}),))],
        PPOConfig(smith_bonus=0.05))
    assert cancelled == [pytest.approx(0.0, abs=1e-6)] * len(cancelled)


def test_smith_bonus_ignores_an_already_upgraded_card_walking_in():
    """A card reward or shop can hand over a card that is already upgraded. The
    upgrade count rises, but no rest site was involved — the deck also grew."""
    from sts2rl.ppo import _upgraded_a_card

    rest = {"room_type": "RestSite"}
    before = DecisionState({"context": rest,
                            "player": {"deck": [{"upgraded": False}, {"upgraded": False}]}}, ())
    bought = DecisionState({"context": rest,
                            "player": {"deck": [{"upgraded": False}, {"upgraded": False},
                                                {"upgraded": True}]}}, ())
    smithed = DecisionState({"context": rest,
                             "player": {"deck": [{"upgraded": True}, {"upgraded": False}]}}, ())

    assert not _upgraded_a_card(before, bought)
    assert _upgraded_a_card(before, smithed)


def test_deck_shaping_charges_for_taking_a_card_and_pays_it_back_on_removal():
    from sts2rl.ppo import DECK_SPAN, _deck_potential

    def screen(size: int, floor: int):
        return DecisionState({
            "decision": "shop", "context": {"act": 1, "floor": floor},
            "player": {"hp": 70, "max_hp": 80, "deck_size": size},
        }, (ActionCandidate("leave_room", {}),))

    coef = 0.3
    grew = _bonus_per_step([screen(18, 5), screen(19, 6)], PPOConfig(deck_shaping_coef=coef))
    shrank = _bonus_per_step([screen(19, 5), screen(18, 6)], PPOConfig(deck_shaping_coef=coef))

    # One card is one DECK_SPAN-th of the potential, charged on the way in and
    # refunded on the way out (up to the gamma factor on the discounted term).
    assert grew[0] == pytest.approx(-coef / DECK_SPAN, abs=2e-3)
    assert shrank[0] == pytest.approx(coef / DECK_SPAN, abs=2e-3)

    # Bounded at both ends so the terminal Phi->0 jump can never exceed the coef,
    # and a screen with no deck holds the previous value instead of dropping to 0.
    assert _deck_potential(screen(4, 1), 0.5) == 0.0
    assert _deck_potential(screen(99, 1), 0.5) == 1.0
    assert _deck_potential(DecisionState({"player": {"hp": 70}}, ()), 0.42) == 0.42


def test_forced_exploration_reaches_an_action_the_policy_never_samples():
    """A bonus cannot revive a collapsed action: PPO's surrogate only has gradient
    on actions that appear in the batch. remove_card was sampled 0 of 220 times,
    so remove_bonus sat unreached for 70 iterations. Forcing is what puts it in."""
    import random as _random
    from sts2rl.ppo import _forced_index

    shop = DecisionState({
        "decision": "shop", "context": {"act": 1, "floor": 7},
        "player": {"hp": 70, "max_hp": 80},
    }, (ActionCandidate("buy_card", {"card_index": 0}),
        ActionCandidate("remove_card", {}),
        ActionCandidate("leave_room", {})))

    always = PPOConfig(explore_epsilon=1.0, explore_actions=("remove_card",))
    assert _forced_index(shop, shop.candidates, always, _random.Random(0)) == 1
    # off by default, and off when the action is not on offer
    assert _forced_index(shop, shop.candidates, PPOConfig(), _random.Random(0)) is None
    no_target = PPOConfig(explore_epsilon=1.0, explore_actions=("select_bundle",))
    assert _forced_index(shop, shop.candidates, no_target, _random.Random(0)) is None

    # roughly epsilon of the opportunities, not all of them
    sometimes = PPOConfig(explore_epsilon=0.1, explore_actions=("remove_card",))
    rng = _random.Random(7)
    hits = sum(_forced_index(shop, shop.candidates, sometimes, rng) is not None
               for _ in range(4000))
    assert 250 < hits < 550


def test_forced_exploration_targets_one_option_id_not_every_choose_option():
    """Rest sites and every event share `choose_option`; forcing the bare action
    would dither the whole event tree instead of the one collapsed choice."""
    import random as _random
    from sts2rl.ppo import _forced_index

    rest = DecisionState({
        "decision": "rest_site", "context": {"act": 1, "floor": 6},
        "player": {"hp": 70, "max_hp": 80},
        "options": [{"index": 0, "option_id": "HEAL"}, {"index": 1, "option_id": "SMITH"}],
    }, (ActionCandidate("choose_option", {"option_index": 0}),
        ActionCandidate("choose_option", {"option_index": 1})))
    event = DecisionState({
        "decision": "event_choice", "context": {"act": 1, "floor": 5},
        "player": {"hp": 70, "max_hp": 80},
        "options": [{"index": 0, "option_id": "TAKE_GOLD"}],
    }, (ActionCandidate("choose_option", {"option_index": 0}),))

    config = PPOConfig(explore_epsilon=1.0, explore_actions=("option:SMITH",))
    assert _forced_index(rest, rest.candidates, config, _random.Random(0)) == 1
    assert _forced_index(event, event.candidates, config, _random.Random(0)) is None


def test_run_episode_hands_the_forced_index_to_the_agent_and_records_it():
    """The seam the unit tests above cannot see: `run_episode` must actually pass
    force_index down, and the trajectory must record the forced action (with the
    policy's own logp for it, so PPO's ratio stays exact)."""
    from sts2rl.agent import AgentStep

    buy = ActionCandidate("buy_card", {"card_index": 0})
    remove = ActionCandidate("remove_card", {})
    shop = DecisionState({
        "decision": "shop", "context": {"act": 1, "floor": 5},
        "player": {"hp": 50, "max_hp": 80, "gold": 999, "deck_size": 18},
    }, (buy, remove))
    win = DecisionState({"decision": "game_over", "victory": True, "act": 1, "floor": 5}, ())

    seen: list[int | None] = []

    class Client:
        def reset(self, _config): return shop
        def step(self, _action): return StepResult(win, terminated=True)

    class Agent:
        def act(self, _raw, candidates, _hidden, *, greedy=False, generator=None,
                force_index=None):
            seen.append(force_index)
            # a collapsed policy: left to itself it would never pick remove_card
            return AgentStep(0 if force_index is None else force_index, -0.2, 0.4, None)

    config = PPOConfig(explore_epsilon=1.0, explore_actions=("remove_card",))
    record = run_episode(Client(), _run_stage(), "seed-explore", Agent(), config)
    assert seen == [1]
    assert record.steps[0].candidates[record.steps[0].index].action == "remove_card"

    # Evaluation must measure the policy, not the policy plus a dither.
    seen.clear()
    greedy_record = run_episode(Client(), _run_stage(), "seed-explore", Agent(), config,
                                greedy=True)
    assert seen == [None]
    assert greedy_record.steps[0].candidates[greedy_record.steps[0].index].action == "buy_card"


def test_deck_deltas_are_ignored_inside_a_fight():
    """`player.deck` is not the deck during combat: a Power card leaves it while
    in play and returns when the fight ends. Observed live on train iteration
    2700 — TAUNT / INFLAME / DOMINATE each took the deck 21 -> 20 on the turn they
    were played and back to 21 at the card reward, so remove_bonus paid out once
    per Power played until this gate was added."""
    from sts2rl.ppo import _deck_potential, _removed_a_card, _upgraded_a_card

    def combat(size: int):
        return DecisionState({
            "decision": "combat_play", "context": {"act": 1, "floor": 4, "room_type": "Monster"},
            "player": {"hp": 60, "max_hp": 80, "deck": [{"upgraded": False}] * size},
        }, ())

    def shop(size: int):
        return DecisionState({
            "decision": "card_select", "context": {"act": 1, "floor": 5, "room_type": "Shop"},
            "player": {"hp": 60, "max_hp": 80, "deck": [{"upgraded": False}] * size},
        }, ())

    assert not _removed_a_card(combat(21), combat(20))  # Inflame entered play
    assert _removed_a_card(shop(21), shop(20))          # a real removal still counts
    # and the potential holds flat rather than dipping and recovering
    assert _deck_potential(combat(30), 0.25) == 0.25


def test_smith_bonus_only_counts_an_upgrade_made_at_a_rest_site():
    """Upgrades also arrive from relics and events — a `buy_relic` step was seen
    raising the count by 2 — and paying the rest-site bonus for those measures
    something other than the HEAL-vs-SMITH choice it exists to move."""
    from sts2rl.ppo import _upgraded_a_card

    def screen(room: str, decision: str, upgraded: int):
        return DecisionState({
            "decision": decision, "context": {"act": 1, "floor": 5, "room_type": room},
            "player": {"hp": 60, "max_hp": 80,
                       "deck": [{"upgraded": i < upgraded} for i in range(4)]},
        }, ())

    assert _upgraded_a_card(screen("RestSite", "card_select", 0),
                            screen("RestSite", "card_select", 1))
    assert not _upgraded_a_card(screen("Shop", "shop", 3), screen("Shop", "shop", 5))
    assert not _upgraded_a_card(screen("Map", "map_select", 1), screen("Map", "map_select", 3))
