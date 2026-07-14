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
from sts2rl.observation import GLOBAL_FEATURE_DIM


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
        with_gather, _ = model(torch.zeros(1, GLOBAL_FEATURE_DIM), entities, features, candidate_slots=slots)
        without_gather, _ = model(torch.zeros(1, GLOBAL_FEATURE_DIM), entities, features)
    assert not torch.allclose(with_gather, without_gather)


def test_targeted_candidates_bind_the_enemy_and_per_target_damage():
    from sts2rl.entities import candidate_entity_bindings
    from sts2rl.features import encode_candidates
    from sts2rl.model import EntityTransformerPolicy
    from sts2rl.protocol import ActionCandidate

    state = {
        **COMBAT_STATE,
        "hand": [{
            **COMBAT_STATE["hand"][0],
            "damage_by_target": [
                {"target_index": 0, "damage": 6},
                {"target_index": 1, "damage": 11},
            ],
        }],
        "enemies": [
            {**COMBAT_STATE["enemies"][0], "index": 0, "id": "MONSTER.A", "hp": 55},
            {**COMBAT_STATE["enemies"][0], "index": 1, "id": "MONSTER.B", "hp": 9},
        ],
    }
    candidates = (
        ActionCandidate("play_card", {"card_index": 0, "target_index": 0}),
        ActionCandidate("play_card", {"card_index": 0, "target_index": 1}),
    )
    observation = normalize_state(state)
    bindings = candidate_entity_bindings(observation, candidates)
    bound_entities = [
        [observation.entities[index] for index in row if index >= 0]
        for row in bindings
    ]
    assert [next(e["id"] for e in row if e["entity_type"] == "enemy")
            for row in bound_entities] == ["MONSTER.A", "MONSTER.B"]
    assert [next(e["damage"] for e in row if e["entity_type"] == "card_target")
            for row in bound_entities] == [6, 11]

    # Counterfactual gate: with identical candidate feature rows, changing only
    # the bound target/content still changes the pointer score.
    torch.manual_seed(0)
    vocab = EntityVocab.from_states([state])
    encoded = encode_entity_batch([observation], vocab)
    model = EntityTransformerPolicy(vocab_size=vocab.size, hidden=32, heads=2, layers=1)
    features = encode_candidates(candidates).unsqueeze(0)
    features[:, 1] = features[:, 0]
    with torch.no_grad():
        logits, _ = model(
            torch.zeros(1, GLOBAL_FEATURE_DIM), encoded, features,
            candidate_slots=torch.tensor([bindings]),
        )
    assert logits[0, 0] != pytest.approx(float(logits[0, 1]))


def test_multiselect_bundle_shop_and_event_candidates_bind_their_contents():
    from sts2rl.entities import candidate_entity_bindings
    from sts2rl.protocol import ActionCandidate

    state = {
        "decision": "card_select",
        "cards": [
            {"index": 0, "id": "CARD.A", "stats": {"damage": 1}},
            {"index": 1, "id": "CARD.B", "stats": {"block": 2}},
            {"index": 2, "id": "CARD.C", "stats": {"damage": 3}},
        ],
        "player": {"hp": 10},
    }
    observation = normalize_state(state)
    candidates = (
        ActionCandidate("select_cards", {"indices": "0,1"}),
        ActionCandidate("select_cards", {"indices": "0,2"}),
    )
    bindings = candidate_entity_bindings(observation, candidates)
    selected = [
        {observation.entities[i]["id"] for i in row if i >= 0
         and observation.entities[i]["entity_type"] == "card"}
        for row in bindings
    ]
    assert selected == [{"CARD.A", "CARD.B"}, {"CARD.A", "CARD.C"}]

    bundle_state = {
        "decision": "bundle_select",
        "bundles": [
            {"index": 0, "cards": [{"id": "CARD.A"}, {"id": "CARD.B"}]},
            {"index": 1, "cards": [{"id": "CARD.C"}]},
        ],
        "player": {"hp": 10},
    }
    bundle_obs = normalize_state(bundle_state)
    bundle_bindings = candidate_entity_bindings(bundle_obs, (
        ActionCandidate("select_bundle", {"bundle_index": 0}),
        ActionCandidate("select_bundle", {"bundle_index": 1}),
    ))
    bundle_cards = [
        {bundle_obs.entities[i]["id"] for i in row if i >= 0
         and bundle_obs.entities[i]["entity_type"] == "bundle_card"}
        for row in bundle_bindings
    ]
    assert bundle_cards == [{"CARD.A", "CARD.B"}, {"CARD.C"}]

    shop_state = {
        "decision": "shop",
        "cards": [{"index": 4, "id": "CARD.SHOP", "stats": {"damage": 8}}],
        "relics": [{"index": 5, "id": "RELIC.SHOP"}],
        "potions": [{"index": 6, "id": "POTION.SHOP"}],
        "player": {"hp": 10},
    }
    shop_obs = normalize_state(shop_state)
    shop_bindings = candidate_entity_bindings(shop_obs, (
        ActionCandidate("buy_card", {"card_index": 4}),
        ActionCandidate("buy_relic", {"relic_index": 5}),
        ActionCandidate("buy_potion", {"potion_index": 6}),
    ))
    assert [shop_obs.entities[row[0]]["id"] for row in shop_bindings] == [
        "CARD.SHOP", "RELIC.SHOP", "POTION.SHOP",
    ]

    event_state = {
        "decision": "event_choice",
        "options": [{
            "index": 0, "text_key": "EVENT.OPTION", "vars": {
                "RandomCard": "Strike", "RandomCardId": "CARD.STRIKE_IRONCLAD",
            },
        }],
        "player": {"hp": 10},
    }
    event_obs = normalize_state(event_state)
    event_binding = candidate_entity_bindings(event_obs, (
        ActionCandidate("choose_option", {"option_index": 0}),
    ))[0]
    ids = {event_obs.entities[i]["id"] for i in event_binding if i >= 0}
    assert "EVENT_VAR.RandomCardId.CARD.STRIKE_IRONCLAD" in ids
    assert "EVENT_VAR.RandomCard" not in ids


def test_shop_stock_and_affordability_are_numeric_features():
    from sts2rl.entities import _entity_numeric

    unavailable = _entity_numeric({"cost": 50, "is_stocked": False, "affordable": False})
    available = _entity_numeric({"cost": 50, "is_stocked": True, "affordable": True})
    assert unavailable[-2:] == (0.0, 0.0)
    assert available[-2:] == (1.0, 1.0)


CARD_REWARD_STATE = {
    "type": "decision",
    "decision": "card_reward",
    "context": {"act": 1, "floor": 3, "room_type": "Monster"},
    "cards": [
        {"index": 0, "id": "CARD.PERFECTED_STRIKE", "name": "Perfected Strike",
         "cost": 2, "type": "Attack", "stats": {"damage": 6}},
        {"index": 1, "id": "CARD.DEFEND_IRONCLAD", "name": "Defend",
         "cost": 1, "type": "Skill", "stats": {"block": 5}},
    ],
    "can_skip": True,
    "player": {
        "name": "The Ironclad", "hp": 60, "max_hp": 80, "gold": 120, "deck_size": 2,
        "deck": [
            {"id": "CARD.STRIKE_IRONCLAD", "name": "Strike", "cost": 1,
             "type": "Attack", "stats": {"damage": 6}},
            {"id": "CARD.BASH", "name": "Bash", "cost": 2,
             "type": "Attack", "stats": {"damage": 8}},
        ],
    },
}


def test_deck_cards_become_entities_with_shared_card_vocab():
    observation = normalize_state(CARD_REWARD_STATE)
    deck_entities = [e for e in observation.entities if e["entity_type"] == "deck_card"]
    assert [e["id"] for e in deck_entities] == ["CARD.STRIKE_IRONCLAD", "CARD.BASH"]

    vocab = EntityVocab.from_states([COMBAT_STATE, CARD_REWARD_STATE])
    # A Strike in the deck hits the same embedding row as a Strike in hand.
    assert vocab.index("deck_card", "CARD.STRIKE_IRONCLAD") == vocab.index("card", "CARD.STRIKE_IRONCLAD")
    assert "deck_card" not in vocab.entries

    batch = encode_entity_batch([observation], vocab)
    assert batch["entity_type"].shape[1] == len(observation.entities)
    # Distinct type ids: offered cards vs deck cards.
    from sts2rl.entities import _KIND_INDEX
    types = batch["entity_type"][0].tolist()
    assert _KIND_INDEX["deck_card"] in types and _KIND_INDEX["card"] in types


def test_candidate_slots_ignore_deck_cards_with_same_id():
    from sts2rl.entities import candidate_entity_slots
    from sts2rl.protocol import ActionCandidate

    state = dict(CARD_REWARD_STATE)
    observation = normalize_state(state)
    candidates = [
        ActionCandidate("select_card_reward", {"card_index": 0}),
        ActionCandidate("skip_card_reward", {}),
    ]
    slots = candidate_entity_slots(observation, candidates)
    picked = observation.entities[slots[0]]
    assert picked["entity_type"] == "card"
    assert picked["id"] == "CARD.PERFECTED_STRIKE"
    assert slots[1] == -1


def test_states_without_deck_are_unchanged():
    observation = normalize_state(COMBAT_STATE)
    assert all(e["entity_type"] != "deck_card" for e in observation.entities)


def test_checkpoint_migration_grows_type_embedding(tmp_path):
    from sts2rl.checkpoint import load_checkpoint, save_checkpoint
    from sts2rl.entities import ENTITY_KINDS
    from sts2rl.model import EntityRecurrentPolicy

    vocab = build_vocab()
    model = EntityRecurrentPolicy(vocab_size=vocab.size, hidden=16, heads=2, layers=1)
    optimizer = torch.optim.AdamW(model.parameters())
    path = tmp_path / "old.pt"
    save_checkpoint(path, model, optimizer, step=3, config={})

    # Simulate a pre-deck checkpoint: one fewer entity kind in the table.
    payload = torch.load(path)
    payload["model"]["type_embed.weight"] = payload["model"]["type_embed.weight"][:-1].clone()
    torch.save(payload, path)

    fresh = EntityRecurrentPolicy(vocab_size=vocab.size, hidden=16, heads=2, layers=1)
    fresh_optimizer = torch.optim.AdamW(fresh.parameters())
    loaded = load_checkpoint(path, fresh, fresh_optimizer)
    assert loaded["migrated_keys"] == ["type_embed.weight"]
    assert loaded.get("optimizer_skipped") is True
    table = fresh.state_dict()["type_embed.weight"]
    assert table.shape[0] == len(ENTITY_KINDS) + 1
    assert torch.all(table[-1] == 0)


def test_checkpoint_same_shape_loads_optimizer(tmp_path):
    from sts2rl.checkpoint import load_checkpoint, save_checkpoint
    from sts2rl.model import EntityRecurrentPolicy

    vocab = build_vocab()
    model = EntityRecurrentPolicy(vocab_size=vocab.size, hidden=16, heads=2, layers=1)
    optimizer = torch.optim.AdamW(model.parameters())
    path = tmp_path / "same.pt"
    save_checkpoint(path, model, optimizer, step=1, config={})

    fresh = EntityRecurrentPolicy(vocab_size=vocab.size, hidden=16, heads=2, layers=1)
    fresh_optimizer = torch.optim.AdamW(fresh.parameters())
    loaded = load_checkpoint(path, fresh, fresh_optimizer)
    assert loaded["migrated_keys"] == []
    assert "optimizer_skipped" not in loaded


# The Act 1 Waterfall Giant is reported "unkillable" with 999,999,999 HP. Scaled
# by _HP_SCALE that is ~1e7, which the forward pass hides (the encoder's
# LayerNorm renormalises) while the attention softmax gradient overflows to inf
# and turns every entity-side grad into NaN — silently destroying the weights
# one PPO step into boss_combat.
SENTINEL_HP_STATE = {
    **COMBAT_STATE,
    "enemies": [
        {
            "index": 0, "id": "MONSTER.WATERFALL_GIANT", "name": "Waterfall Giant",
            "hp": 999999999, "max_hp": 999999999, "block": 0,
            "intents": [{"type": "Stun"}], "intends_attack": False, "powers": None,
        }
    ],
}


def test_entity_numeric_bounds_engine_sentinels():
    from sts2rl.entities import _entity_numeric

    features = _entity_numeric(SENTINEL_HP_STATE["enemies"][0])
    assert len(features) == ENTITY_NUMERIC_DIM
    # The bound is asserted against a literal, not against _FEATURE_LIMIT: raising
    # the constant must not be able to quietly satisfy this test. Unbounded, hp
    # alone lands at 1e7 here.
    assert max(abs(value) for value in features) <= 10.0
    # real content is untouched by the bound
    ordinary = _entity_numeric(COMBAT_STATE["enemies"][0])
    assert ordinary[0] == pytest.approx(0.55)
    assert ordinary[1] == pytest.approx(0.55)


def test_encode_entity_batch_bounds_the_tensor_fed_to_the_model():
    vocab = EntityVocab.from_states([COMBAT_STATE, EVENT_STATE, SENTINEL_HP_STATE])
    batch = encode_entity_batch([normalize_state(SENTINEL_HP_STATE)], vocab)
    assert torch.isfinite(batch["entity_numeric"]).all()
    assert float(batch["entity_numeric"].abs().max()) <= 10.0
