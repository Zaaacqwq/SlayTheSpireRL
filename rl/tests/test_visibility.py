from sts2rl.entities import EntityVocab
from sts2rl.ppo import EpisodeRecord, StoredStep
from sts2rl.protocol import ActionCandidate
from sts2rl.visibility import visibility_audit


def _record(state, candidates, chosen=0):
    return EpisodeRecord("seed", [
        StoredStep(state, tuple(candidates), chosen, 0.0, 0.0, 0.0, None),
    ], False, False)


def test_visibility_audit_reports_offered_and_chosen_zeroes():
    state = {
        "decision": "combat_play", "player": {},
        "hand": [{"index": 0, "id": "CARD.X"}],
        "enemies": [{"index": 0, "id": "MONSTER.X", "hp": 10}],
    }
    vocab = EntityVocab.from_states([state])
    report = visibility_audit([_record(state, [
        ActionCandidate("play_card", {"card_index": 0, "target_index": 0}),
        ActionCandidate("end_turn", {}),
    ])], vocab)
    assert report["offered_actions"]["play_card"] == 1
    assert report["chosen_actions"]["end_turn"] == 0
    assert report["never_chosen_actions"] == ["end_turn"]
    assert "buy_card" in report["never_offered_actions"]
    assert report["violations"] == []


def test_visibility_audit_fails_unknown_entities_and_pointer_misses():
    state = {"decision": "card_select", "player": {}, "cards": []}
    report = visibility_audit([_record(state, [
        ActionCandidate("select_cards", {"indices": "0"}),
    ])], EntityVocab({}))
    assert report["pointer_misses"] == {"select_cards": 1}
    assert any(item.startswith("pointer_misses") for item in report["violations"])


def test_visibility_audit_detects_semantic_candidate_collisions():
    state = {"decision": "combat_play", "player": {}}
    candidate = ActionCandidate("end_turn", {})
    report = visibility_audit([_record(state, [candidate, candidate])], EntityVocab({}))
    assert report["candidate_collisions"] == 1
