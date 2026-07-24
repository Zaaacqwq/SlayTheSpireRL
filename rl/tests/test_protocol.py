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


def test_affordable_shop_inventory_and_removal_are_reachable():
    state = {
        "decision": "shop",
        "cards": [{"index": 0, "id": "CARD.A", "affordable": True}],
        "relics": [{"index": 1, "id": "RELIC.B", "affordable": True}],
        "potions": [{"index": 2, "id": "POTION.C", "affordable": True}],
        "can_remove_card": True,
    }
    assert [a.action for a in legal_actions(state)] == [
        "buy_card", "buy_relic", "buy_potion", "remove_card", "leave_room",
    ]


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


# The engine serialises potions only under `player`, with no `can_use` flag
# (RunSimulator.cs:3042 -> {index, id, name, description, vars, target_type}).
# legal_actions used to read a top-level `potions` key and gate on `can_use`, so
# it never emitted a single use_potion candidate: every policy from v1 to v5
# reached the Act 1 boss carrying a median of three potions it could not drink.
def _combat_with_potions(potions, enemies=None):
    return {
        "decision": "combat_play",
        "hand": [{"index": 0, "can_play": True, "target_type": "Self"}],
        "enemies": enemies if enemies is not None else [{"index": 0, "hp": 40}],
        "player": {"hp": 50, "max_hp": 80, "potions": potions},
    }


def test_potions_are_offered_from_the_player_object():
    state = _combat_with_potions([
        {"index": 0, "id": "POTION.FORTIFIER", "target_type": "Self"},
        {"index": 2, "id": "POTION.BLOOD_POTION", "target_type": "Self"},
    ])
    potion_actions = [a for a in legal_actions(state) if a.action == "use_potion"]
    assert [a.args for a in potion_actions] == [{"potion_index": 0}, {"potion_index": 2}]


def test_any_enemy_potion_expands_over_alive_enemies():
    state = _combat_with_potions(
        [{"index": 1, "id": "POTION.FIRE_POTION", "target_type": "AnyEnemy"}],
        enemies=[{"index": 0, "hp": 30}, {"index": 1, "hp": 0}, {"index": 2, "hp": 12}],
    )
    potion_actions = [a for a in legal_actions(state) if a.action == "use_potion"]
    # the engine rejects an AnyEnemy potion without target_index while several
    # enemies live (RunSimulator.cs DoUsePotion), and the dead one is not a target
    assert [a.args for a in potion_actions] == [
        {"potion_index": 1, "target_index": 0},
        {"potion_index": 1, "target_index": 2},
    ]


def test_no_potions_offers_no_potion_actions():
    state = _combat_with_potions([])
    assert not [a for a in legal_actions(state) if a.action == "use_potion"]


def test_explicit_can_use_false_is_respected():
    state = _combat_with_potions([
        {"index": 0, "id": "POTION.X", "target_type": "Self", "can_use": False},
    ])
    assert not [a for a in legal_actions(state) if a.action == "use_potion"]


def test_card_select_caps_selection_size_to_the_binding_budget():
    # A select_cards binds one entity per referenced card PLUS its stat/enchant/
    # affliction facts, so only small selections fit MAX_CANDIDATE_BINDINGS. A large
    # max_select must be capped to SELECT_CARDS_MAX_SIZE (picking fewer is legal) —
    # briefly enumerating the full range emitted a 17-binding candidate that raised
    # inside the batched inference thread and killed the whole batch.
    from sts2rl.protocol import SELECT_CARDS_MAX_SIZE
    state = {
        "decision": "card_select", "player": {},
        "cards": [{"index": i} for i in range(20)],
        "min_select": 1, "max_select": 20,
    }
    actions = legal_actions(state)
    assert actions and all(a.action == "select_cards" for a in actions)  # min_select > 0, no skip
    assert len(actions) <= 256  # SELECT_CARDS_CANDIDATE_BUDGET
    assert all(1 <= len(a.args["indices"].split(",")) <= SELECT_CARDS_MAX_SIZE for a in actions)


def test_mandatory_select_larger_than_cap_falls_back_to_confirm():
    # A mandatory select of more cards than can be bound is unrepresentable, so it
    # must degrade to confirm rather than raise or emit an over-limit candidate.
    actions = legal_actions({
        "decision": "card_select", "player": {},
        "cards": [{"index": i} for i in range(30)],
        "min_select": 5, "max_select": 8,  # min > SELECT_CARDS_MAX_SIZE (4)
    })
    assert [a.action for a in actions] == ["skip_select"]


def test_degenerate_card_select_never_raises():
    # A mandatory selection we cannot enumerate (no cards) must still yield a legal
    # action (confirm) rather than raise and poison the iteration.
    actions = legal_actions({
        "decision": "card_select", "player": {},
        "cards": [], "min_select": 1, "max_select": 1,
    })
    assert [a.action for a in actions] == ["skip_select"]
