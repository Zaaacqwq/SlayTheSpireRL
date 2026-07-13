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
    "map",
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
    # Deck contents as first-class entities: card picks, removals and rest-site
    # upgrades cannot condition on synergy when the deck is only a size scalar.
    ("player", "deck", "deck_card"),
)


def _map_node_entities(map_payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Flatten the full-act map into annotated ``map_node`` entities.

    Reachability and depth are computed from the current position over the
    ``children`` edges: roots are the current node's children (or the first
    row at act start), depth counts moves from the current position. Nodes
    behind or on abandoned branches stay ``reachable=0, depth=0`` — the
    ``visited`` flag is what distinguishes the walked path.
    """
    nodes: list[dict[str, Any]] = []
    for row_nodes in map_payload.get("rows") or []:
        if isinstance(row_nodes, list):
            nodes.extend(dict(node) for node in row_nodes if isinstance(node, Mapping))
    boss = map_payload.get("boss")
    if isinstance(boss, Mapping):
        boss_node = dict(boss)
        # entity_key checks id/name before type; the boss's monster id and
        # localized name must not shadow its room-type vocabulary key.
        boss_node["boss_id"] = boss_node.pop("id", None)
        boss_node["boss_name"] = boss_node.pop("name", None)
        nodes.append(boss_node)

    by_coord = {(node.get("col"), node.get("row")): node for node in nodes}
    current = next((node for node in nodes if node.get("current")), None)
    if current is not None:
        frontier = [(child.get("col"), child.get("row"))
                    for child in current.get("children") or [] if isinstance(child, Mapping)]
    else:
        first_row = min((node.get("row") for node in nodes if node.get("row") is not None), default=None)
        frontier = [coord for coord, node in by_coord.items() if node.get("row") == first_row]

    depth = 1
    seen: set[tuple[Any, Any]] = set()
    while frontier:
        next_frontier: list[tuple[Any, Any]] = []
        for coord in frontier:
            node = by_coord.get(coord)
            if node is None or coord in seen:
                continue
            seen.add(coord)
            node["reachable"] = True
            node["depth"] = depth
            next_frontier.extend(
                (child.get("col"), child.get("row"))
                for child in node.get("children") or [] if isinstance(child, Mapping)
            )
        frontier = next_frontier
        depth += 1

    for node in nodes:
        node.setdefault("reachable", False)
        node.setdefault("depth", 0)
        node.pop("children", None)
        node["entity_type"] = "map_node"
        node["id"] = node.get("id", UNK_ID)
    return nodes


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
    map_payload = state.get("map")
    if isinstance(map_payload, Mapping):
        entities.extend(_map_node_entities(map_payload))
    return NormalizedObservation(
        str(state.get("decision", state.get("type", "unknown"))),
        global_features, tuple(entities), dict(state), warnings,
    )
