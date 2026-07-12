"""Entity-level encoding: id vocabularies, phase ids, and padded entity batches.

The roadmap fixes the architecture to variable entity encoding with a phase
embedding; unknown content maps to ``UNK`` (index 0) and produces a warning
instead of failing, because new game content must not crash training.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import torch
from torch import Tensor

from .observation import NormalizedObservation, normalize_state
from .protocol import DECISIONS

ENTITY_KINDS: tuple[str, ...] = ("card", "enemy", "relic", "potion", "choice", "option", "power")
_KIND_INDEX: Mapping[str, int] = {kind: index + 1 for index, kind in enumerate(ENTITY_KINDS)}

PHASES: tuple[str, ...] = tuple(sorted(DECISIONS))
_PHASE_INDEX: Mapping[str, int] = {phase: index for index, phase in enumerate(PHASES)}

UNK_INDEX = 0

# hp, max_hp, block, cost, stat_damage, stat_block, upgraded, can_play,
# intends_attack, intent_damage, index
ENTITY_NUMERIC_DIM = 11
_HP_SCALE = 100.0
_SMALL_SCALE = 10.0


def phase_id(phase: str) -> int:
    return _PHASE_INDEX[phase]


def entity_key(entity: Mapping[str, Any]) -> str:
    """Stable vocabulary key. Cards expose ``id``; enemies/relics only names."""
    for name in ("id", "name", "text", "label"):
        value = entity.get(name)
        if isinstance(value, str) and value and value != "UNK":
            return value
    return "UNK"


def _entity_numeric(entity: Mapping[str, Any]) -> tuple[float, ...]:
    def number(value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    stats = entity.get("stats") or {}
    stats = stats if isinstance(stats, Mapping) else {}
    intents = entity.get("intents") or []
    intent_damage = sum(
        number(intent.get("damage")) for intent in intents if isinstance(intent, Mapping)
    )
    return (
        number(entity.get("hp")) / _HP_SCALE,
        number(entity.get("max_hp")) / _HP_SCALE,
        number(entity.get("block")) / _HP_SCALE,
        number(entity.get("cost")) / _SMALL_SCALE,
        number(stats.get("damage")) / _HP_SCALE,
        number(stats.get("block")) / _HP_SCALE,
        1.0 if entity.get("upgraded") else 0.0,
        1.0 if entity.get("can_play") else 0.0,
        1.0 if entity.get("intends_attack") else 0.0,
        intent_damage / _HP_SCALE,
        number(entity.get("index")) / _SMALL_SCALE,
    )


@dataclass
class EntityVocab:
    """Per-kind id vocabulary sharing one contiguous embedding index space.

    Index 0 is ``UNK`` for every kind; lookups of unseen content return it and
    queue a warning that the caller drains via :meth:`consume_warnings`.
    """

    entries: dict[str, dict[str, int]]
    _warnings: list[str] = field(default_factory=list)
    _warned: set[str] = field(default_factory=set)

    @property
    def size(self) -> int:
        return 1 + sum(len(kind_entries) for kind_entries in self.entries.values())

    @classmethod
    def from_states(cls, states: Iterable[Mapping[str, Any]]) -> "EntityVocab":
        entries: dict[str, dict[str, int]] = {kind: {} for kind in ENTITY_KINDS}
        next_index = 1
        for state in states:
            for entity in normalize_state(state).entities:
                kind = str(entity.get("entity_type"))
                key = entity_key(entity)
                if kind in entries and key != "UNK" and key not in entries[kind]:
                    entries[kind][key] = next_index
                    next_index += 1
        return cls(entries)

    def index(self, kind: str, key: str) -> int:
        found = self.entries.get(kind, {}).get(key)
        if found is not None:
            return found
        tag = f"unknown_entity:{kind}:{key}"
        if tag not in self._warned:
            self._warned.add(tag)
            self._warnings.append(tag)
        return UNK_INDEX

    def consume_warnings(self) -> tuple[str, ...]:
        drained = tuple(self._warnings)
        self._warnings.clear()
        return drained

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "entries": self.entries}
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "EntityVocab":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("version") != 1:
            raise ValueError(f"unsupported vocab version in {path}")
        return cls({kind: dict(entries) for kind, entries in payload["entries"].items()})


def encode_entity_batch(
    observations: Sequence[NormalizedObservation], vocab: EntityVocab
) -> dict[str, Tensor]:
    """Pad variable entity lists into one batch; padded slots are zeroed.

    A row with no entities keeps one zero UNK token unmasked so the
    transformer never sees a fully-padded row (which yields NaN attention).
    """
    if not observations:
        raise ValueError("cannot encode an empty observation batch")
    width = max(1, max(len(observation.entities) for observation in observations))
    types = torch.zeros((len(observations), width), dtype=torch.long)
    ids = torch.zeros((len(observations), width), dtype=torch.long)
    numeric = torch.zeros((len(observations), width, ENTITY_NUMERIC_DIM), dtype=torch.float32)
    mask = torch.zeros((len(observations), width), dtype=torch.bool)
    phases = torch.zeros((len(observations),), dtype=torch.long)
    for row, observation in enumerate(observations):
        phases[row] = phase_id(observation.phase)
        if not observation.entities:
            mask[row, 0] = True
            continue
        for slot, entity in enumerate(observation.entities):
            kind = str(entity.get("entity_type"))
            types[row, slot] = _KIND_INDEX.get(kind, 0)
            ids[row, slot] = vocab.index(kind, entity_key(entity))
            numeric[row, slot] = torch.tensor(_entity_numeric(entity), dtype=torch.float32)
            mask[row, slot] = True
    return {
        "entity_type": types,
        "entity_id": ids,
        "entity_numeric": numeric,
        "entity_mask": mask,
        "phase": phases,
    }
