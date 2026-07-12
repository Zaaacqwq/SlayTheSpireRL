from __future__ import annotations

import json

import pytest

torch = pytest.importorskip("torch")

from sts2rl.entities import (
    ENTITY_NUMERIC_DIM,
    EntityVocab,
    PHASES,
    encode_entity_batch,
    phase_id,
)
from sts2rl.observation import normalize_state


COMBAT_STATE = {
    "type": "decision",
    "decision": "combat_play",
    "context": {"act": 1, "floor": 2, "room_type": "Monster"},
    "round": 1,
    "energy": 3,
    "max_energy": 3,
    "hand": [
        {
            "index": 0, "id": "CARD.STRIKE_IRONCLAD", "name": "Strike", "cost": 1,
            "type": "Attack", "rarity": "Basic", "can_play": True,
            "target_type": "AnyEnemy", "stats": {"damage": 6},
        },
        {
            "index": 1, "id": "CARD.DEFEND_IRONCLAD", "name": "Defend", "cost": 1,
            "type": "Skill", "rarity": "Basic", "can_play": True,
            "target_type": "Self", "stats": {"block": 5},
        },
    ],
    "enemies": [
        {
            "index": 0, "name": "Fuzzy Wurm Crawler", "hp": 55, "max_hp": 55,
            "block": 0, "intents": [{"type": "Attack", "damage": 4}],
            "intends_attack": True, "powers": None,
        }
    ],
    "player": {"name": "The Ironclad", "hp": 80, "max_hp": 80, "block": 0, "gold": 99},
}

EVENT_STATE = {
    "type": "decision",
    "decision": "event_choice",
    "context": {"act": 1, "floor": 1},
    "event_name": "Mysterious Shrine",
    "options": [
        {"index": 0, "name": "Pray", "is_enabled": True},
        {"index": 1, "name": "Leave", "is_enabled": True},
    ],
    "player": {"hp": 80, "max_hp": 80, "gold": 99},
}


def build_vocab() -> EntityVocab:
    return EntityVocab.from_states([COMBAT_STATE, EVENT_STATE])


def test_vocab_indexes_known_entities_above_unk():
    vocab = build_vocab()
    assert vocab.index("card", "CARD.STRIKE_IRONCLAD") > 0
    assert vocab.index("enemy", "Fuzzy Wurm Crawler") > 0
    assert vocab.index("option", "Pray") > 0


def test_vocab_maps_unknown_to_unk_and_warns():
    vocab = build_vocab()
    assert vocab.index("card", "CARD.NEVER_SEEN") == 0
    assert any("CARD.NEVER_SEEN" in w for w in vocab.consume_warnings())
    # consumed warnings are not reported twice
    assert vocab.consume_warnings() == ()


def test_vocab_round_trips_through_json(tmp_path):
    vocab = build_vocab()
    path = tmp_path / "vocab.json"
    vocab.save(path)
    loaded = EntityVocab.load(path)
    assert loaded.index("card", "CARD.DEFEND_IRONCLAD") == vocab.index("card", "CARD.DEFEND_IRONCLAD")
    assert json.loads(path.read_text())["version"] == 1


def test_phase_ids_cover_protocol_decisions_and_reject_unknown():
    assert len(set(phase_id(p) for p in PHASES)) == len(PHASES)
    with pytest.raises(KeyError):
        phase_id("not_a_phase")


def test_encode_entity_batch_pads_and_masks():
    vocab = build_vocab()
    observations = [normalize_state(COMBAT_STATE), normalize_state(EVENT_STATE)]
    batch = encode_entity_batch(observations, vocab)
    n_combat = len(observations[0].entities)
    n_event = len(observations[1].entities)
    width = max(n_combat, n_event)
    assert batch["entity_type"].shape == (2, width)
    assert batch["entity_id"].shape == (2, width)
    assert batch["entity_numeric"].shape == (2, width, ENTITY_NUMERIC_DIM)
    assert batch["entity_mask"].tolist() == [
        [True] * n_combat + [False] * (width - n_combat),
        [True] * n_event + [False] * (width - n_event),
    ]
    assert batch["phase"].tolist() == [phase_id("combat_play"), phase_id("event_choice")]
    # padded slots must be all-zero so they cannot leak content
    assert batch["entity_id"][1, n_event:].abs().sum() == 0
    assert batch["entity_numeric"][1, n_event:].abs().sum() == 0


def test_encode_entity_batch_is_deterministic():
    vocab = build_vocab()
    observations = [normalize_state(COMBAT_STATE)]
    a = encode_entity_batch(observations, vocab)
    b = encode_entity_batch(observations, vocab)
    for key in a:
        assert torch.equal(a[key], b[key])


def test_transformer_policy_masks_padded_candidates():
    from sts2rl.model import EntityTransformerPolicy

    vocab = build_vocab()
    observations = [normalize_state(COMBAT_STATE), normalize_state(EVENT_STATE)]
    entities = encode_entity_batch(observations, vocab)
    batch, candidates = 2, 5
    candidate_features = torch.randn(batch, candidates, 16)
    candidate_mask = torch.tensor([[True] * 5, [True, True, False, False, False]])
    global_features = torch.randn(batch, 8)
    model = EntityTransformerPolicy(
        vocab_size=vocab.size, global_dim=8, candidate_dim=16, hidden=32,
        heads=2, layers=1,
    )
    logits, value = model(global_features, entities, candidate_features, candidate_mask)
    assert logits.shape == (batch, candidates)
    assert value.shape == (batch,)
    minimum = torch.finfo(logits.dtype).min
    assert (logits[1, 2:] == minimum).all()
    assert (logits[0] > minimum).all()


def test_transformer_policy_learns_a_fixed_batch():
    from sts2rl.model import EntityTransformerPolicy

    vocab = build_vocab()
    torch.manual_seed(0)
    observations = [normalize_state(COMBAT_STATE), normalize_state(EVENT_STATE)]
    entities = encode_entity_batch(observations, vocab)
    candidate_features = torch.randn(2, 4, 16)
    candidate_mask = torch.tensor([[True, True, True, True], [True, True, False, False]])
    global_features = torch.randn(2, 8)
    targets = torch.tensor([3, 1])
    model = EntityTransformerPolicy(
        vocab_size=vocab.size, global_dim=8, candidate_dim=16, hidden=32,
        heads=2, layers=1,
    )
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-2)
    first = last = None
    for _ in range(60):
        logits, _ = model(global_features, entities, candidate_features, candidate_mask)
        loss = torch.nn.functional.cross_entropy(logits, targets)
        optimizer.zero_grad(); loss.backward(); optimizer.step()
        first = float(loss.detach()) if first is None else first
        last = float(loss.detach())
    assert last < first * 0.2


def test_candidate_entity_slots_align_candidates_to_entities():
    from sts2rl.entities import candidate_entity_slots
    from sts2rl.protocol import ActionCandidate

    obs = normalize_state(COMBAT_STATE)
    candidates = (
        ActionCandidate("play_card", {"card_index": 1}),
        ActionCandidate("end_turn", {}),
    )
    slots = candidate_entity_slots(obs, candidates)
    assert slots[1] == -1
    assert obs.entities[slots[0]]["id"] == "CARD.DEFEND_IRONCLAD"

    map_state = {
        "decision": "map_select",
        "choices": [{"col": 0, "row": 1, "type": "Monster"}, {"col": 2, "row": 1, "type": "RestSite"}],
        "player": {"hp": 10},
    }
    map_obs = normalize_state(map_state)
    map_candidates = (
        ActionCandidate("select_map_node", {"col": 2, "row": 1}),
        ActionCandidate("select_map_node", {"col": 0, "row": 1}),
    )
    map_slots = candidate_entity_slots(map_obs, map_candidates)
    assert map_obs.entities[map_slots[0]]["type"] == "RestSite"
    assert map_obs.entities[map_slots[1]]["type"] == "Monster"


def test_pointer_gather_changes_logits():
    from sts2rl.entities import candidate_entity_slots
    from sts2rl.model import EntityTransformerPolicy
    from sts2rl.protocol import ActionCandidate
    from sts2rl.features import encode_candidates

    vocab = build_vocab()
    torch.manual_seed(0)
    obs = normalize_state(COMBAT_STATE)
    entities = encode_entity_batch([obs], vocab)
    candidates = (
        ActionCandidate("play_card", {"card_index": 0}),
        ActionCandidate("play_card", {"card_index": 1}),
    )
    features = encode_candidates(candidates).unsqueeze(0)
    slots = torch.tensor([candidate_entity_slots(obs, candidates)])
    model = EntityTransformerPolicy(vocab_size=vocab.size, hidden=32, heads=2, layers=1)
    with torch.no_grad():
        with_gather, _ = model(torch.zeros(1, 12), entities, features, candidate_slots=slots)
        without_gather, _ = model(torch.zeros(1, 12), entities, features)
    assert not torch.allclose(with_gather, without_gather)
