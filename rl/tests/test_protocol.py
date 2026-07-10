import pytest

from sts2rl.protocol import ProtocolError, canonical_state_hash, legal_actions, parse_state


def test_hash_is_key_order_stable_and_ignores_logging_fields():
    assert canonical_state_hash({"b": 2, "a": 1, "timestamp": "x"}) == canonical_state_hash({"a": 1, "b": 2, "timestamp": "y"})


def test_combat_candidates_expand_enemy_targets():
    state = {"decision": "combat_play", "hand": [{"index": 2, "can_play": True, "target_type": "AnyEnemy"}], "enemies": [{"index": 0, "hp": 5}, {"index": 1, "hp": 0}]}
    actions = legal_actions(state)
    assert [a.action for a in actions] == ["play_card", "end_turn"]
    assert actions[0].args == {"card_index": 2, "target_index": 0}


def test_unknown_phase_is_not_silently_dropped():
    with pytest.raises(ProtocolError):
        parse_state({"decision": "new_overlay"})


def test_map_requires_stable_coordinates():
    with pytest.raises(ProtocolError):
        legal_actions({"decision": "map_select", "available_nodes": [{"index": 0}]})
