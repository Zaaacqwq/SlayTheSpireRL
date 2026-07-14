from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

UNK_ID = "UNK"

# hp, max_hp, block, gold, energy, max_energy, act, floor, round,
# deck_size, draw_pile_count, discard_pile_count, orb_slots
GLOBAL_FEATURE_DIM = 13

# Top-level fields of every decision shape recorded in
# rl/schema/m0_observed_schema.json plus the fixture-confirmed extras.
_KNOWN_FIELDS = frozenset({
    "type", "decision", "context", "player",
    "hand", "enemies", "player_powers", "round", "energy", "max_energy",
    "draw_pile_count", "discard_pile_count",
    "cards", "can_skip", "gold_earned",
    "choices", "options", "bundles", "act", "act_name", "floor",
    "event_id", "event_name", "description",
    "relics", "potions", "card_removal_cost", "can_remove_card",
    "min_select", "max_select", "victory", "score", "message",
    "from_event", "map", "orbs", "orb_slots", "stars", "osty",
})

# (container, field, entity kind); ``None`` container means the state root.
_ENTITY_GROUPS: tuple[tuple[str | None, str, str], ...] = (
    (None, "hand", "card"),
    (None, "cards", "card"),
    (None, "enemies", "enemy"),
    (None, "relics", "relic"),
    (None, "potions", "potion"),
    (None, "choices", "choice"),
    (None, "options", "option"),
    (None, "player_powers", "power"),
    ("player", "relics", "relic"),
    ("player", "potions", "potion"),
    # Deck contents as first-class entities: card picks, removals and rest-site
    # upgrades cannot condition on synergy when the deck is only a size scalar.
    ("player", "deck", "deck_card"),
)

_GLOBAL_SCALES = (
    100.0, 100.0, 100.0, 500.0, 10.0, 10.0,
    3.0, 60.0, 50.0, 50.0, 50.0, 50.0,
    10.0,
)


def _number(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if number == number and abs(number) != float("inf") else 0.0


def _normalized_globals(values: tuple[Any, ...]) -> tuple[float, ...]:
    return tuple(max(-10.0, min(10.0, _number(value) / scale))
                 for value, scale in zip(values, _GLOBAL_SCALES))


def _add_entity(entities: list[Mapping[str, Any]], item: Mapping[str, Any], kind: str,
                **extra: Any) -> None:
    entity = dict(item)
    entity.update(extra)
    entity["entity_type"] = kind
    entity["id"] = entity.get("id", UNK_ID)
    entities.append(entity)


@dataclass(frozen=True)
class NormalizedObservation:
    phase: str
    global_features: tuple[float, ...]
    entities: tuple[Mapping[str, Any], ...]
    raw: Mapping[str, Any]
    warnings: tuple[str, ...] = ()


def normalize_state(state: Mapping[str, Any]) -> NormalizedObservation:
    player = state.get("player") or {}
    context = state.get("context") or {}
    warnings = tuple(
        f"unknown_state_field:{key}" for key in state if key not in _KNOWN_FIELDS
    )
    global_features = _normalized_globals((
        player.get("hp", 0), player.get("max_hp", 0),
        player.get("block", 0), player.get("gold", 0),
        state.get("energy", 0), state.get("max_energy", 0),
        context.get("act", state.get("act", 0)) or 0,
        context.get("floor", state.get("floor", 0)) or 0,
        state.get("round", 0), player.get("deck_size", 0),
        state.get("draw_pile_count", 0), state.get("discard_pile_count", 0),
        state.get("orb_slots", 0),
    ))
    entities: list[Mapping[str, Any]] = []
    event_id = state.get("event_id")
    if isinstance(event_id, str) and event_id:
        # Which event this is changes what its options mean; give the encoder
        # the event identity as a first-class entity.
        _add_entity(entities, {"id": event_id}, "event")
    boss = context.get("boss") or ((state.get("map") or {}).get("boss") if isinstance(state.get("map"), Mapping) else None)
    if isinstance(boss, Mapping) and boss.get("id"):
        _add_entity(entities, boss, "boss")
    for container, field, kind in _ENTITY_GROUPS:
        source = state if container is None else (state.get(container) or {})
        for item in source.get(field) or []:
            if isinstance(item, Mapping):
                _add_entity(entities, item, kind)

    # A bundle's identity is its bound contents; the outer row only supplies a
    # stable anchor for bundle_index and must not become UNK.
    for bundle in state.get("bundles") or []:
        if isinstance(bundle, Mapping):
            _add_entity(entities, {**bundle, "id": "BUNDLE"}, "choice")

    # Nested combat facts need their own tokens. Keeping them buried under an
    # enemy/card dictionary made them completely invisible to encode_entity_batch.
    for enemy in state.get("enemies") or []:
        if not isinstance(enemy, Mapping):
            continue
        owner = enemy.get("index", -1)
        for power in enemy.get("powers") or []:
            if isinstance(power, Mapping):
                _add_entity(entities, power, "enemy_power", owner_index=owner)
        for intent in enemy.get("intents") or []:
            if isinstance(intent, Mapping):
                _add_entity(entities, {**intent, "id": f"INTENT.{intent.get('type', 'UNKNOWN')}"},
                            "intent", owner_index=owner)

    card_groups = (
        ("hand", state.get("hand") or []),
        ("cards", state.get("cards") or []),
        ("deck", player.get("deck") or []),
    )
    for card_source, cards in card_groups:
        for position, card in enumerate(cards):
            if not isinstance(card, Mapping):
                continue
            card_index = card.get("index", position)
            for target in card.get("damage_by_target") or []:
                if isinstance(target, Mapping):
                    _add_entity(entities, {**target, "id": "CARD_TARGET.DAMAGE"}, "card_target",
                                card_index=card_index, card_source=card_source)
            for field, kind in (("enchantment", "enchantment"), ("affliction", "affliction")):
                value = card.get(f"{field}_id") or card.get(field)
                if value:
                    _add_entity(entities, {
                        "id": str(value), "amount": card.get(f"{field}_amount", 0),
                        "card_index": card_index, "card_source": card_source,
                    }, kind)
            for key, value in (card.get("stats") or {}).items():
                if isinstance(value, (int, float)):
                    _add_entity(entities, {
                        "id": f"CARD_STAT.{key}", "amount": value,
                        "card_index": card_index, "card_source": card_source,
                    }, "card_stat")

    for bundle in state.get("bundles") or []:
        if not isinstance(bundle, Mapping):
            continue
        bundle_index = bundle.get("index", -1)
        for position, card in enumerate(bundle.get("cards") or []):
            if isinstance(card, Mapping):
                _add_entity(entities, card, "bundle_card",
                            bundle_index=bundle_index, card_index=position)

    for orb in state.get("orbs") or []:
        if isinstance(orb, Mapping):
            _add_entity(entities, orb, "orb")

    full_map = state.get("map") or {}
    if isinstance(full_map, Mapping):
        for row in full_map.get("rows") or []:
            for node in row or []:
                if not isinstance(node, Mapping):
                    continue
                _add_entity(entities, {**node, "id": f"ROOM.{node.get('type', 'Unknown')}"}, "map_node")
                for child in node.get("children") or []:
                    if isinstance(child, Mapping):
                        _add_entity(entities, {
                            "id": "MAP_EDGE", "from_col": node.get("col", 0),
                            "from_row": node.get("row", 0), "col": child.get("col", 0),
                            "row": child.get("row", 0),
                        }, "map_edge")

    for option in state.get("options") or []:
        if not isinstance(option, Mapping):
            continue
        option_vars = option.get("vars") or {}
        for key, value in option_vars.items():
            # The rendered localized name is intentionally redundant once the
            # engine supplies the stable ModelId companion.
            if key == "RandomCard" and option_vars.get("RandomCardId"):
                continue
            # Identity-valued vars (for example RandomCardId) must include the
            # referenced content. Numeric vars share an id and carry magnitude.
            entity_id = f"EVENT_VAR.{key}"
            if isinstance(value, str) and key.lower().endswith("id"):
                entity_id += f".{value}"
            _add_entity(entities, {
                "id": entity_id, "amount": value if isinstance(value, (int, float)) else 0,
                "option_index": option.get("index", -1), "value_key": None if isinstance(value, (int, float)) else str(value),
            }, "event_var")
    return NormalizedObservation(
        str(state.get("decision", state.get("type", "unknown"))),
        global_features, tuple(entities), dict(state), warnings,
    )
