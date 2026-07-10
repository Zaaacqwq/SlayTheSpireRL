from sts2rl.observation import normalize_state
from sts2rl.protocol import ActionCandidate


def test_normalization_preserves_entities_and_warns_unknown():
    obs = normalize_state({"decision": "combat_play", "player": {"hp": 5}, "hand": [{"id": "CARD.X"}], "future_field": 1})
    assert obs.entities[0]["entity_type"] == "card"
    assert "unknown_state_field:future_field" in obs.warnings
