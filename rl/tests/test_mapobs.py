"""v6 map observation: full-act map nodes as entities with reachability."""
from __future__ import annotations

import pytest

torch = pytest.importorskip("torch")

from sts2rl.checkpoint import load_checkpoint, save_checkpoint
from sts2rl.entities import (
    ENTITY_NUMERIC_DIM,
    EntityVocab,
    _KIND_INDEX,
    candidate_entity_slots,
    encode_entity_batch,
)
from sts2rl.model import EntityRecurrentPolicy
from sts2rl.observation import normalize_state
from sts2rl.protocol import ActionCandidate


def _node(col, row, node_type, children=(), visited=False, current=False):
    return {
        "col": col, "row": row, "type": node_type,
        "children": [{"col": c, "row": r} for c, r in children],
        "visited": visited, "current": current,
    }


# Two branches from the current node: left goes Monster -> RestSite, right goes
# Elite -> Shop. A third row-0 start (col 4) was never taken; its subtree at
# (4, 1) is unreachable from the current position.
MAP_PAYLOAD = {
    "rows": [
        [
            _node(0, 0, "Monster", children=[(0, 1), (2, 1)], visited=True, current=True),
            _node(4, 0, "Monster", children=[(4, 1)]),
        ],
        [
            _node(0, 1, "Monster", children=[(1, 2)]),
            _node(2, 1, "Elite", children=[(1, 2)]),
            _node(4, 1, "Shop", children=[(1, 2)]),
        ],
        [_node(1, 2, "RestSite", children=[(1, 3)])],
    ],
    "boss": {"col": 1, "row": 3, "type": "Boss", "id": "HEXAGHOST_BOSS", "name": "Hexaghost"},
    "current_coord": {"col": 0, "row": 0},
}

MAP_SELECT_STATE = {
    "type": "decision",
    "decision": "map_select",
    "context": {"act": 1, "floor": 1},
    "choices": [
        {"col": 0, "row": 1, "type": "Monster"},
        {"col": 2, "row": 1, "type": "Elite"},
    ],
    "player": {"hp": 30, "max_hp": 80, "gold": 50, "deck_size": 10},
    "map": MAP_PAYLOAD,
}

ROOM_VOCAB_STATE = {
    "type": "decision",
    "decision": "map_select",
    "choices": [
        {"col": 0, "row": 1, "type": t}
        for t in ("Monster", "Elite", "RestSite", "Shop", "Boss")
    ],
    "player": {"hp": 10},
}


def _map_entities(observation):
    return {
        (e["col"], e["row"]): e
        for e in observation.entities if e["entity_type"] == "map_node"
    }


def test_map_nodes_annotated_with_reachability_and_depth():
    observation = normalize_state(MAP_SELECT_STATE)
    nodes = _map_entities(observation)
    assert len(nodes) == 7  # 6 map points + boss

    current = nodes[(0, 0)]
    assert current["current"] and current["visited"]
    assert not current["reachable"] and current["depth"] == 0

    assert nodes[(0, 1)]["reachable"] and nodes[(0, 1)]["depth"] == 1
    assert nodes[(2, 1)]["reachable"] and nodes[(2, 1)]["depth"] == 1
    assert nodes[(1, 2)]["reachable"] and nodes[(1, 2)]["depth"] == 2
    boss = nodes[(1, 3)]
    assert boss["reachable"] and boss["depth"] == 3

    # The abandoned start and its subtree are not reachable any more.
    assert not nodes[(4, 0)]["reachable"]
    assert not nodes[(4, 1)]["reachable"]


def test_boss_identity_does_not_shadow_room_type_key():
    observation = normalize_state(MAP_SELECT_STATE)
    boss = _map_entities(observation)[(1, 3)]
    assert boss["boss_id"] == "HEXAGHOST_BOSS"

    vocab = EntityVocab.from_states([ROOM_VOCAB_STATE])
    batch = encode_entity_batch([observation], vocab)
    # Every map node keys on its room type via the choice vocabulary: no UNK.
    map_positions = [
        slot for slot, e in enumerate(observation.entities)
        if e["entity_type"] == "map_node"
    ]
    assert all(batch["entity_id"][0, slot].item() != 0 for slot in map_positions)
    assert vocab.consume_warnings() == ()
    assert "map_node" not in vocab.entries


def test_act_start_uses_first_row_as_roots():
    payload = {
        "rows": [
            [_node(0, 0, "Monster", children=[(0, 1)]),
             _node(2, 0, "Monster", children=[(0, 1)])],
            [_node(0, 1, "Elite")],
        ],
        "boss": None,
        "current_coord": None,
    }
    state = {**MAP_SELECT_STATE, "map": payload,
             "choices": [{"col": 0, "row": 0, "type": "Monster"}]}
    nodes = _map_entities(normalize_state(state))
    assert nodes[(0, 0)]["depth"] == 1 and nodes[(2, 0)]["depth"] == 1
    assert nodes[(0, 1)]["depth"] == 2


def test_map_candidates_point_at_map_nodes_with_choice_fallback():
    candidates = [
        ActionCandidate("select_map_node", {"col": 0, "row": 1}),
        ActionCandidate("select_map_node", {"col": 2, "row": 1}),
    ]
    with_map = normalize_state(MAP_SELECT_STATE)
    slots = candidate_entity_slots(with_map, candidates)
    assert [with_map.entities[s]["entity_type"] for s in slots] == ["map_node", "map_node"]
    assert with_map.entities[slots[0]]["type"] == "Monster"
    assert with_map.entities[slots[1]]["type"] == "Elite"

    without_map = normalize_state({k: v for k, v in MAP_SELECT_STATE.items() if k != "map"})
    slots = candidate_entity_slots(without_map, candidates)
    assert [without_map.entities[s]["entity_type"] for s in slots] == ["choice", "choice"]


def test_states_without_map_are_unchanged():
    state = {k: v for k, v in MAP_SELECT_STATE.items() if k != "map"}
    observation = normalize_state(state)
    assert all(e["entity_type"] != "map_node" for e in observation.entities)
    assert observation.warnings == ()


def test_entity_numeric_dim_covers_map_features():
    assert ENTITY_NUMERIC_DIM == 17
    vocab = EntityVocab.from_states([ROOM_VOCAB_STATE])
    batch = encode_entity_batch([normalize_state(MAP_SELECT_STATE)], vocab)
    numeric = batch["entity_numeric"][0]
    observation = normalize_state(MAP_SELECT_STATE)
    kinds = [e["entity_type"] for e in observation.entities]
    # visited/current/reachable/depth live in the last four slots, zero for
    # non-map entities and populated for map nodes.
    for slot, kind in enumerate(kinds):
        tail = numeric[slot, -4:]
        if kind == "map_node":
            continue
        assert torch.all(tail == 0), kind
    map_slots = [i for i, k in enumerate(kinds) if k == "map_node"]
    assert any(numeric[slot, -4:].abs().sum() > 0 for slot in map_slots)


def test_checkpoint_migration_grows_numeric_columns_and_type_rows(tmp_path):
    vocab = EntityVocab.from_states([ROOM_VOCAB_STATE])
    model = EntityRecurrentPolicy(vocab_size=vocab.size, hidden=16, heads=2, layers=1)
    optimizer = torch.optim.AdamW(model.parameters())
    path = tmp_path / "old.pt"
    save_checkpoint(path, model, optimizer, step=5, config={})

    # Simulate a pre-map checkpoint: one fewer entity kind, 13 numeric inputs.
    payload = torch.load(path)
    payload["model"]["type_embed.weight"] = payload["model"]["type_embed.weight"][:-1].clone()
    payload["model"]["numeric.weight"] = payload["model"]["numeric.weight"][:, :13].clone()
    torch.save(payload, path)

    fresh = EntityRecurrentPolicy(vocab_size=vocab.size, hidden=16, heads=2, layers=1)
    fresh_optimizer = torch.optim.AdamW(fresh.parameters())
    loaded = load_checkpoint(path, fresh, fresh_optimizer)
    assert sorted(loaded["migrated_keys"]) == ["numeric.weight", "type_embed.weight"]
    assert loaded.get("optimizer_skipped") is True
    grown = fresh.state_dict()["numeric.weight"]
    assert grown.shape[1] == ENTITY_NUMERIC_DIM
    assert torch.all(grown[:, 13:] == 0)


def test_migrated_model_identical_on_map_free_states(tmp_path):
    torch.manual_seed(7)
    vocab = EntityVocab.from_states([ROOM_VOCAB_STATE])
    original = EntityRecurrentPolicy(vocab_size=vocab.size, hidden=16, heads=2, layers=1)
    path = tmp_path / "old.pt"
    save_checkpoint(path, original, torch.optim.AdamW(original.parameters()), step=1, config={})
    payload = torch.load(path)
    payload["model"]["type_embed.weight"] = payload["model"]["type_embed.weight"][:-1].clone()
    payload["model"]["numeric.weight"] = payload["model"]["numeric.weight"][:, :13].clone()
    torch.save(payload, path)

    migrated = EntityRecurrentPolicy(vocab_size=vocab.size, hidden=16, heads=2, layers=1)
    load_checkpoint(path, migrated)
    original.eval(); migrated.eval()

    candidates = [
        ActionCandidate("select_map_node", {"col": 0, "row": 1}),
        ActionCandidate("select_map_node", {"col": 2, "row": 1}),
    ]
    from sts2rl.features import encode_candidates, encode_global

    def logits(state):
        observation = normalize_state(state)
        entities = encode_entity_batch([observation], vocab)
        slots = torch.tensor([candidate_entity_slots(observation, candidates)])
        out_original, _, _ = original(
            encode_global(state).unsqueeze(0), entities,
            encode_candidates(candidates).unsqueeze(0), candidate_slots=slots)
        out_migrated, _, _ = migrated(
            encode_global(state).unsqueeze(0), entities,
            encode_candidates(candidates).unsqueeze(0), candidate_slots=slots)
        return out_original, out_migrated

    map_free = {k: v for k, v in MAP_SELECT_STATE.items() if k != "map"}
    with torch.no_grad():
        same_a, same_b = logits(map_free)
        diff_a, diff_b = logits(MAP_SELECT_STATE)
    # The dropped weights only ever touched zero inputs on map-free states.
    assert torch.allclose(same_a, same_b, atol=1e-6)
    assert not torch.allclose(diff_a, diff_b, atol=1e-6)
