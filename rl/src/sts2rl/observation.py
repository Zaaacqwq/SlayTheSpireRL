from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

UNK_ID = "UNK"


@dataclass(frozen=True)
class NormalizedObservation:
    phase: str
    global_features: tuple[float, ...]
    entities: tuple[Mapping[str, Any], ...]
    raw: Mapping[str, Any]
    warnings: tuple[str, ...] = ()


def normalize_state(state: Mapping[str, Any]) -> NormalizedObservation:
    player = state.get("player", {})
    context = state.get("context", {})
    warnings: list[str] = []
    known = {"type", "decision", "context", "player", "hand", "enemies", "potions", "relics", "choices", "cards", "options", "energy", "max_energy", "round", "floor", "act"}
    for key in state:
        if key not in known:
            warnings.append(f"unknown_state_field:{key}")
    global_features = (
        float(player.get("hp", 0)), float(player.get("max_hp", 0)),
        float(player.get("gold", 0)), float(state.get("energy", 0)),
        float(state.get("max_energy", 0)), float(context.get("act", state.get("act", 0))),
        float(context.get("floor", state.get("floor", 0))), float(state.get("round", 0)),
    )
    entities: list[Mapping[str, Any]] = []
    for group, kind in (("hand", "card"), ("cards", "card"), ("enemies", "enemy"), ("relics", "relic"), ("potions", "potion"), ("choices", "choice"), ("options", "option")):
        for item in state.get(group, []) or []:
            if isinstance(item, Mapping):
                entity = dict(item)
                entity["entity_type"] = kind
                entity["id"] = entity.get("id", UNK_ID)
                entities.append(entity)
    return NormalizedObservation(str(state.get("decision", state.get("type", "unknown"))), global_features, tuple(entities), dict(state), tuple(warnings))
