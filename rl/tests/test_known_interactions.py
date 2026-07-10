"""Cross-checks the simulator's damage math against real numbers observed
live during the 2026-07-09 Phase A verification playthrough (Ironclad,
Act 1, floor 2 vs. a Shrinker Beetle). These are not invented test
fixtures -- they're the actual `get_game_state` deltas from that session,
used here as the "manually-played real run" cross-check called for in
plan/plan.md's Phase B verification section.

Sequence actually played (floor 2, SHRINKER_BEETLE_0, 40 max HP):
  1. Bash (8 base dmg, no buffs active yet)        -> enemy HP 40 -> 32  (delta 8)
     Bash also applies 2 stacks of Vulnerable to the target.
  2. Strike+ (9 base dmg) while target has 1 Vulnerable stack remaining
     (2 applied, 1 already ticked off by an intervening enemy turn)
                                                     -> enemy HP 32 -> 19  (delta 13)
  3. (later, same fight) Strike (6 base dmg), Vulnerable expired
                                                     -> enemy HP 19 -> 13  (delta 6)
"""

from __future__ import annotations

import random

import pytest

from sim import powers
from sim.card import CardInstance, load_card_defs
from sim.combat import Combat


def test_bash_plain_hit_matches_observed_8_damage():
    """No buffs/debuffs active: Bash's 8 base damage should land as exactly 8."""
    assert powers.compute_attack_damage(8, target_vulnerable_stacks=0) == 8


def test_strike_plus_into_vulnerable_matches_observed_13_damage():
    """This is the calibration point for the floor()-after-percent rounding
    rule: 9 base damage with 1 Vulnerable stack landed as 13 damage live,
    i.e. floor(9 * 1.5) == 13 (not round() or ceil(), which would give 14)."""
    assert powers.compute_attack_damage(9, target_vulnerable_stacks=1) == 13


def test_plain_strike_after_vulnerable_expired_matches_observed_6_damage():
    """Later in the same fight, once Vulnerable had ticked off, a plain
    6-damage Strike landed as exactly 6 -- confirms Vulnerable expiry
    isn't leaking a bonus into unrelated hits."""
    assert powers.compute_attack_damage(6, target_vulnerable_stacks=0) == 6


def test_bash_then_strike_plus_end_to_end_matches_observed_21_total_damage():
    """Replays the real Bash -> Strike+ sequence through the actual Combat
    state machine (not just the powers.py formula in isolation), against a
    dummy single-enemy encounter, and checks the combined HP delta matches
    what really happened: 40 -> 32 -> 19, a total drop of 21."""
    defs = load_card_defs()
    rng = random.Random(0)
    monster_data = {
        "monsters": {
            "DUMMY_40HP": {
                "display_name": "dummy",
                "hp": 40,
                "ai_cycle": [{}],  # does nothing on its turn; isolates the player's damage
            }
        }
    }
    deck = [CardInstance(card_id="BASH"), CardInstance(card_id="STRIKE_IRONCLAD", is_upgraded=True)]
    combat = Combat(
        deck=deck,
        enemy_ids=["DUMMY_40HP"],
        card_defs=defs,
        monster_data=monster_data,
        rng=rng,
    )
    enemy = combat.enemies[0]
    assert enemy.hp == 40

    bash_index = next(i for i, c in enumerate(combat.player.hand) if c.card_id == "BASH")
    combat.play_card(bash_index, target_index=0)
    assert enemy.hp == 32  # matches the real 40 -> 32 delta

    strike_index = next(
        i for i, c in enumerate(combat.player.hand) if c.card_id == "STRIKE_IRONCLAD"
    )
    combat.play_card(strike_index, target_index=0)
    assert enemy.hp == 19  # matches the real 32 -> 19 delta

    assert 40 - enemy.hp == 21


def test_dismantle_double_hits_when_target_vulnerable():
    """Dismantle's real rules text ("如果该敌人有易伤状态，则攻击两次" --
    "attacks twice if the target has Vulnerable") combined with the
    Vulnerable formula calibrated above: base 8 damage, target Vulnerable,
    should land as two floor(8 * 1.5) = 12 hits, 24 total."""
    defs = load_card_defs()
    rng = random.Random(0)
    monster_data = {
        "monsters": {
            "DUMMY_100HP": {"display_name": "dummy", "hp": 100, "ai_cycle": [{}]}
        }
    }
    combat = Combat(
        deck=[CardInstance(card_id="DISMANTLE")],
        enemy_ids=["DUMMY_100HP"],
        card_defs=defs,
        monster_data=monster_data,
        rng=rng,
    )
    enemy = combat.enemies[0]
    enemy.vulnerable = 1
    combat.play_card(0, target_index=0)
    assert 100 - enemy.hp == 24


def test_dismantle_single_hit_when_target_not_vulnerable():
    defs = load_card_defs()
    rng = random.Random(0)
    monster_data = {
        "monsters": {
            "DUMMY_100HP": {"display_name": "dummy", "hp": 100, "ai_cycle": [{}]}
        }
    }
    combat = Combat(
        deck=[CardInstance(card_id="DISMANTLE")],
        enemy_ids=["DUMMY_100HP"],
        card_defs=defs,
        monster_data=monster_data,
        rng=rng,
    )
    enemy = combat.enemies[0]
    combat.play_card(0, target_index=0)
    assert 100 - enemy.hp == 8


# --- Stage 2: triggered-Power system (plan/plan.md 2026-07-10 entry) ---


def _dummy_combat(deck, defs=None, hp=100):
    defs = defs or load_card_defs()
    monster_data = {"monsters": {"DUMMY": {"display_name": "dummy", "hp": hp, "ai_cycle": [{}]}}}
    return Combat(deck=deck, enemy_ids=["DUMMY"], card_defs=defs, monster_data=monster_data, rng=random.Random(0))


def _play(combat, defs, card_id, target_index=None):
    idx = next(i for i, c in enumerate(combat.player.hand) if c.card_id == card_id)
    combat.play_card(idx, target_index=target_index)


def test_demon_form_grants_strength_every_turn_start_not_immediately():
    """Demon Form (turn_start trigger): playing it grants no immediate
    Strength -- only the *next* turn's start (and every one after) does."""
    defs = load_card_defs()
    combat = _dummy_combat([CardInstance(card_id="DEMON_FORM")], defs)
    _play(combat, defs, "DEMON_FORM")
    assert combat.player.strength == 0
    combat.end_turn()
    assert combat.player.strength == 2
    combat.end_turn()
    assert combat.player.strength == 4


def test_cruelty_applies_after_vulnerable_multiplicatively():
    """Cruelty (Ironclad Power): a Vulnerable-affected hit gets a second,
    independently-floored +25% on top of Vulnerable's +50% --
    floor(floor(8 * 1.5) * 1.25) = floor(12 * 1.25) = 15, not the naive
    8 * 1.75 = 14 a single combined multiplier would give."""
    defs = load_card_defs()
    combat = _dummy_combat([CardInstance(card_id="CRUELTY"), CardInstance(card_id="BASH")], defs)
    _play(combat, defs, "CRUELTY")
    enemy = combat.enemies[0]
    enemy.vulnerable = 1
    _play(combat, defs, "BASH", target_index=0)
    assert 100 - enemy.hp == 15


def test_unmovable_doubles_only_the_first_block_gain_each_turn():
    defs = load_card_defs()
    deck = [CardInstance(card_id="UNMOVABLE")] + [CardInstance(card_id="DEFEND_IRONCLAD") for _ in range(2)]
    combat = _dummy_combat(deck, defs)
    _play(combat, defs, "UNMOVABLE")
    combat.end_turn()  # fresh energy for two Defends (Unmovable=2 energy already spent turn 1)
    _play(combat, defs, "DEFEND_IRONCLAD")
    assert combat.player.block == 10  # first block gain this turn doubled (5 -> 10)
    _play(combat, defs, "DEFEND_IRONCLAD")
    assert combat.player.block == 15  # second one this turn: not doubled, plain +5


def test_dark_embrace_draws_when_any_card_is_exhausted():
    """True Grit exhausts a random *other* hand card; Dark Embrace should
    react to that exhaust even though Dark Embrace wasn't the card played."""
    defs = load_card_defs()
    deck = [CardInstance(card_id="DARK_EMBRACE"), CardInstance(card_id="TRUE_GRIT")] + [
        CardInstance(card_id="STRIKE_IRONCLAD") for _ in range(3)
    ]
    combat = _dummy_combat(deck, defs)
    _play(combat, defs, "DARK_EMBRACE")
    hand_before = len(combat.player.hand)
    _play(combat, defs, "TRUE_GRIT")
    # -1 True Grit itself (discarded), -1 random other card (exhausted),
    # +1 Dark Embrace's on-exhaust draw -> net -1.
    assert len(combat.player.hand) == hand_before - 1


def test_barricade_prevents_block_reset_at_turn_start():
    defs = load_card_defs()
    deck = [CardInstance(card_id="BARRICADE"), CardInstance(card_id="DEFEND_IRONCLAD")]
    combat = _dummy_combat(deck, defs)
    _play(combat, defs, "BARRICADE")
    combat.end_turn()  # fresh energy for Defend (Barricade costs all 3 energy turn 1)
    _play(combat, defs, "DEFEND_IRONCLAD")
    assert combat.player.block == 5
    combat.end_turn()
    assert combat.player.block == 5  # would be 0 without Barricade
