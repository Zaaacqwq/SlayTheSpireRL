from __future__ import annotations

from .diff_harness import first_mismatch, semantic_projection


def _state(*, hp: int = 30, energy: int = 3):
    return {
        "decision": "combat_play",
        "energy": energy,
        "draw_pile_count": 5,
        "discard_pile_count": 0,
        "player": {"hp": 80, "block": 0},
        "player_powers": None,
        "hand": [{"id": "CARD.STRIKE_IRONCLAD"}],
        "enemies": [{
            "id": "MONSTER.SHRINKER_BEETLE", "index": 0,
            "hp": hp, "block": 0, "powers": None,
            "intents": [{"type": "Attack", "damage": 7, "total_damage": 7}],
        }],
    }


def test_projection_compares_hand_as_multiset():
    state = _state()
    state["hand"] = [
        {"id": "CARD.DEFEND_IRONCLAD"},
        {"id": "CARD.STRIKE_IRONCLAD"},
        {"id": "CARD.STRIKE_IRONCLAD"},
    ]
    assert semantic_projection(state)["hand"] == {
        "CARD.DEFEND_IRONCLAD": 1,
        "CARD.STRIKE_IRONCLAD": 2,
    }


def test_first_mismatch_reports_precise_field():
    mismatch = first_mismatch(_state(hp=30), _state(hp=29))
    assert mismatch is not None
    assert mismatch.field == "enemies[0].hp"
    assert mismatch.real == 30
    assert mismatch.sim == 29


def test_equivalent_states_have_no_mismatch():
    assert first_mismatch(_state(), _state()) is None
