"""Unit tests for Stage 2.5's sim/run.py (run continuity: HP carryover +
simple reward-card picks across a sequence of Combats). Uses deliberately
trivial dummy monsters (1 HP one-shot-kill, or a one-shot-kill attacker) so
each test can force a specific fight outcome deterministically, the same
style test_known_interactions.py already uses for Combat itself.
"""

from __future__ import annotations

import random

import pytest

from sim.card import CardInstance, load_card_defs
from sim.run import Run


def _monster_data(hp=1, attack=None):
    """A single dummy encounter, reused across all fights in a run (Run
    doesn't care that every fight is against the "same" monster id -- it
    only inspects Combat.outcome/HP, not which encounter it came from)."""
    ai_cycle = [{"attack": {"amount": attack}}] if attack else [{}]
    return {
        "monsters": {"DUMMY": {"display_name": "dummy", "hp": hp, "ai_cycle": ai_cycle}},
        "encounters": {"DUMMY_SOLO": {"enemies": ["DUMMY"]}},
    }


def _win_current_fight(run: Run, defs) -> None:
    """Plays the first legal damage-dealing attack against the single
    enemy; DUMMY has 1 HP so any Strike/Bash from the starter deck kills it
    in one hit."""
    hand_index = next(
        i for i, ci in enumerate(run.combat.player.hand) if defs[ci.card_id].target == "single_enemy"
    )
    run.play_card(hand_index, target_index=0)


def test_hp_carries_over_between_fights_not_reset_to_full():
    """DUMMY hits for 11 on its own turn; if the player just ends their
    first turn without blocking, they take exactly 11 damage before winning
    the fight on their next turn -- next fight should start at that same
    reduced HP, not back at max_hp."""
    defs = load_card_defs()
    run = Run(card_defs=defs, monster_data=_monster_data(hp=1, attack=11), rng=random.Random(0), player_max_hp=80)
    run.end_turn()  # DUMMY attacks for 11 on its turn
    assert run.combat.player.hp == 69
    _win_current_fight(run, defs)
    assert run.combat.outcome == "win"
    assert run.hp == 69
    assert run.phase == "reward_pick"

    run.skip_reward()
    assert run.phase == "combat"
    assert run.fight_index == 1
    assert run.combat.player.hp == 69  # carried over, not reset to 80


def test_picked_reward_card_is_added_to_deck_and_drawable_next_fight():
    defs = load_card_defs()
    run = Run(card_defs=defs, monster_data=_monster_data(hp=1), rng=random.Random(0), player_max_hp=80)
    deck_size_before = len(run.deck)
    _win_current_fight(run, defs)
    assert run.phase == "reward_pick"
    chosen_id = run.pending_reward_choices[0]

    run.pick_reward(0)
    assert len(run.deck) == deck_size_before + 1
    assert any(ci.card_id == chosen_id for ci in run.deck)
    # the new fight's Combat was built from the updated deck -- the chosen
    # card must be reachable somewhere in its piles (hand or draw pile).
    assert any(ci.card_id == chosen_id for ci in run.combat.whole_deck())


def test_skip_reward_leaves_deck_size_unchanged():
    defs = load_card_defs()
    run = Run(card_defs=defs, monster_data=_monster_data(hp=1), rng=random.Random(0), player_max_hp=80)
    deck_size_before = len(run.deck)
    _win_current_fight(run, defs)
    run.skip_reward()
    assert len(run.deck) == deck_size_before


def test_run_lost_when_player_dies():
    defs = load_card_defs()
    run = Run(card_defs=defs, monster_data=_monster_data(hp=1, attack=200), rng=random.Random(0), player_max_hp=80)
    run.end_turn()  # DUMMY one-shots the player for 200
    assert run.combat.outcome == "loss"
    assert run.outcome == "run_lost"


def test_run_won_after_all_fights_cleared_no_reward_pick_after_final_win():
    defs = load_card_defs()
    run = Run(
        card_defs=defs, monster_data=_monster_data(hp=1), rng=random.Random(0), player_max_hp=80, fights_per_run=3
    )
    for fight in range(3):
        _win_current_fight(run, defs)
        assert run.combat.outcome == "win"
        if fight < 2:
            assert run.phase == "reward_pick"
            assert run.outcome is None
            run.skip_reward()
    assert run.outcome == "run_won"
    assert run.fight_index == 2  # never advanced past the final fight's index


def test_actions_illegal_in_wrong_phase_raise():
    defs = load_card_defs()
    run = Run(card_defs=defs, monster_data=_monster_data(hp=1), rng=random.Random(0), player_max_hp=80)
    with pytest.raises(RuntimeError):
        run.pick_reward(0)  # still in combat phase
    _win_current_fight(run, defs)
    assert run.phase == "reward_pick"
    with pytest.raises(RuntimeError):
        run.end_turn()  # no longer in combat phase
