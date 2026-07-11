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


def test_upstream_action_names_are_used():
    reward = legal_actions({"decision": "card_reward", "cards": [{"index": 0}]})
    assert reward[0].action == "select_card_reward"
    shop = legal_actions({"decision": "shop", "cards": [], "relics": [], "potions": []})
    assert shop[-1].action == "leave_room"


def test_combat_card_reward_remains_skippable():
    # The from_event suppression below must not leak into ordinary combat rewards,
    # where skipping is a real choice rather than a no-op back into the same state.
    combat = legal_actions({"decision": "card_reward", "cards": [{"index": 0}]})
    assert [candidate.action for candidate in combat] == ["select_card_reward", "skip_card_reward"]

    unskippable = legal_actions({"decision": "card_reward", "cards": [{"index": 0}], "can_skip": False})
    assert [candidate.action for candidate in unskippable] == ["select_card_reward"]


def test_forced_card_reward_does_not_offer_noop_skip():
    forced = legal_actions({"decision": "card_reward", "cards": [{"index": 0}], "from_event": True})
    assert [candidate.action for candidate in forced] == ["select_card_reward"]
