from sts2rl.observation import GLOBAL_FEATURE_DIM, normalize_state
from sts2rl.protocol import ActionCandidate


def test_normalization_preserves_entities_and_warns_unknown():
    obs = normalize_state({"decision": "combat_play", "player": {"hp": 5}, "hand": [{"id": "CARD.X"}], "future_field": 1})
    assert obs.entities[0]["entity_type"] == "card"
    assert "unknown_state_field:future_field" in obs.warnings


def test_normalization_extracts_player_nested_relics_and_potions():
    obs = normalize_state({
        "decision": "combat_play",
        "player": {
            "hp": 50, "max_hp": 70, "block": 3, "gold": 99, "deck_size": 10,
            "relics": [{"id": "RELIC.BURNING_BLOOD", "name": "Burning Blood"}],
            "potions": [{"index": 0, "id": "POTION.BLOCK_POTION", "name": "Block Potion"}],
        },
        "player_powers": [{"id": "POWER.STRENGTH", "name": "Strength", "amount": 2}],
        "hand": [], "enemies": [],
        "round": 2, "energy": 2, "max_energy": 3,
        "draw_pile_count": 4, "discard_pile_count": 6,
    })
    kinds = [e["entity_type"] for e in obs.entities]
    assert "relic" in kinds and "potion" in kinds and "power" in kinds
    assert obs.warnings == ()


def test_global_features_cover_combat_counters():
    obs = normalize_state({
        "decision": "combat_play",
        "context": {"act": 1, "floor": 2},
        "player": {"hp": 50, "max_hp": 70, "block": 3, "gold": 99, "deck_size": 10},
        "round": 2, "energy": 2, "max_energy": 3,
        "draw_pile_count": 4, "discard_pile_count": 6,
        "orb_slots": 4,
    })
    assert len(obs.global_features) == GLOBAL_FEATURE_DIM
    assert obs.global_features == (
        .5, .7, .03, .198, .2, .3, 1 / 3, 2 / 60, 2 / 50, .2, .08, .12, .4,
    )


def test_nested_combat_facts_and_boss_become_entities():
    obs = normalize_state({
        "decision": "combat_play",
        "context": {"boss": {"id": "THE_KIN_BOSS"}},
        "player": {},
        "enemies": [{
            "index": 2, "id": "MONSTER.X",
            "powers": [{"id": "POWER.STRENGTH_POWER", "amount": 7}],
            "intents": [{"type": "Buff"}],
        }],
    })
    by_kind = {kind: [e for e in obs.entities if e["entity_type"] == kind]
               for kind in ("boss", "enemy_power", "intent")}
    assert by_kind["boss"][0]["id"] == "THE_KIN_BOSS"
    assert by_kind["enemy_power"][0]["amount"] == 7
    assert by_kind["enemy_power"][0]["owner_index"] == 2
    assert by_kind["intent"][0]["id"] == "INTENT.Buff"


def test_known_real_schema_fields_do_not_warn():
    obs = normalize_state({
        "type": "decision", "decision": "card_reward", "context": {},
        "cards": [{"index": 0, "id": "CARD.X"}], "can_skip": True,
        "gold_earned": 15, "player": {"hp": 10},
    })
    assert obs.warnings == ()
