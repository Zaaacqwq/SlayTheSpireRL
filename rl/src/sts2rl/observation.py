from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

UNK_ID = "UNK"

# hp, max_hp, block, gold, energy, max_energy, act, floor, round,
# deck_size, draw_pile_count, discard_pile_count
GLOBAL_FEATURE_DIM = 12

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
})

# (container, field, entity kind); ``None`` container means the state root.
_ENTITY_GROUPS: tuple[tuple[str | None, str, str], ...] = (
    (None, "hand", "card"),
    (None, "cards", "card"),
    (None, "enemies", "enemy"),
    (None, "relics", "relic"),
    (None, "potions", "potion"),
    (None, "choices", "choice"),
    (None, "bundles", "choice"),
    (None, "options", "option"),
    (None, "player_powers", "power"),
    ("player", "relics", "relic"),
    ("player", "potions", "potion"),
)


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
    global_features = (
        float(player.get("hp", 0)), float(player.get("max_hp", 0)),
        float(player.get("block", 0)), float(player.get("gold", 0)),
        float(state.get("energy", 0)), float(state.get("max_energy", 0)),
        float(context.get("act", state.get("act", 0)) or 0),
        float(context.get("floor", state.get("floor", 0)) or 0),
        float(state.get("round", 0)),
        float(player.get("deck_size", 0)),
        float(state.get("draw_pile_count", 0)),
        float(state.get("discard_pile_count", 0)),
    )
    entities: list[Mapping[str, Any]] = []
    event_id = state.get("event_id")
    if isinstance(event_id, str) and event_id:
        # Which event this is changes what its options mean; give the encoder
        # the event identity as a first-class entity.
        entities.append({"entity_type": "event", "id": event_id})
    for container, field, kind in _ENTITY_GROUPS:
        source = state if container is None else (state.get(container) or {})
        for item in source.get(field) or []:
            if isinstance(item, Mapping):
                entity = dict(item)
                entity["entity_type"] = kind
                entity["id"] = entity.get("id", UNK_ID)
                entities.append(entity)
    return NormalizedObservation(
        str(state.get("decision", state.get("type", "unknown"))),
        global_features, tuple(entities), dict(state), warnings,
    )
