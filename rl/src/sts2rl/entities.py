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

ENTITY_KINDS: tuple[str, ...] = (
    "card", "enemy", "relic", "potion", "choice", "option", "power", "event",
    # Appended last so pre-deck checkpoints keep their type-embedding rows.
    "deck_card",
    "enemy_power", "intent", "boss", "bundle_card", "card_target",
    "card_stat", "orb", "enchantment", "affliction", "map_node",
    "map_edge", "event_var",
)
_KIND_INDEX: Mapping[str, int] = {kind: index + 1 for index, kind in enumerate(ENTITY_KINDS)}

# Kinds sharing another kind's id vocabulary: a Strike in the deck must hit the
# same embedding as a Strike in hand, and candidate->entity slot matching stays
# unambiguous because the kinds remain distinct.
_VOCAB_KIND_ALIASES: Mapping[str, str] = {
    "deck_card": "card", "bundle_card": "card", "enemy_power": "power",
}

PHASES: tuple[str, ...] = tuple(sorted(DECISIONS))
_PHASE_INDEX: Mapping[str, int] = {phase: index for index, phase in enumerate(PHASES)}

UNK_INDEX = 0

# Base combat fields followed by generic owner/value/graph fields used by nested
# power, intent, bundle, map and per-target damage tokens.
ENTITY_NUMERIC_DIM = 30
_HP_SCALE = 100.0
_SMALL_SCALE = 10.0

# The engine reports sentinel stats for scripted content — the Act 1 Waterfall
# Giant is "unkillable" with 999,999,999 HP — which scale to ~1e7 here. The
# forward pass hides it (the encoder's LayerNorm renormalises), but attention
# scores then saturate and their softmax gradient overflows to inf, so backward
# yields NaN grads and silently destroys the weights. Bound every field: no
# legitimate value comes close, so this is a no-op on real content.
_FEATURE_LIMIT = 10.0


def extend_vocab_entries(
    existing: Mapping[str, Mapping[str, int]],
    discovered: Mapping[str, Iterable[str]],
) -> dict[str, dict[str, int]]:
    """Append new ids without changing any checkpoint-visible index.

    All entity kinds share one embedding table. Re-sorting a regenerated kind
    would therefore shift every later kind and silently attach learned weights
    to the wrong content. Existing indices are immutable; newly reviewed ids
    are sorted only among themselves and appended to the global tail.
    """
    entries = {kind: dict(rows) for kind, rows in existing.items()}
    indexes = sorted(index for rows in entries.values() for index in rows.values())
    if indexes != list(range(1, len(indexes) + 1)):
        raise ValueError("existing vocabulary indices must be unique and contiguous")
    next_index = len(indexes) + 1
    for kind, keys in discovered.items():
        rows = entries.setdefault(kind, {})
        for key in sorted(set(keys)):
            if key == "UNK" or key in rows:
                continue
            rows[key] = next_index
            next_index += 1
    return entries


def phase_id(phase: str) -> int:
    return _PHASE_INDEX[phase]


def entity_key(entity: Mapping[str, Any]) -> str:
    """Stable vocabulary key.

    Cards/enemies/relics/potions/powers expose a prefixed ``id``; map choices
    only carry a room ``type`` (Monster/Elite/Rest/...) which is exactly the
    semantic worth embedding for routing.
    """
    # text_key (event options) and name (rest options: C# type name) are the
    # stable identities; localized title is a last resort.
    for name in ("id", "text_key", "name", "text", "label", "title", "type"):
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

    def bounded(value: float) -> float:
        if value != value:  # NaN
            return 0.0
        return max(-_FEATURE_LIMIT, min(_FEATURE_LIMIT, value))

    stats = entity.get("stats") or {}
    stats = stats if isinstance(stats, Mapping) else {}
    intents = entity.get("intents") or []
    intent_damage = sum(
        number(intent.get("damage")) for intent in intents if isinstance(intent, Mapping)
    )
    return tuple(bounded(value) for value in (
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
        number(entity.get("col")) / _SMALL_SCALE,
        number(entity.get("row")) / _SMALL_SCALE,
        number(entity.get("amount")) / _SMALL_SCALE,
        number(entity.get("hits", entity.get("repeat"))) / _SMALL_SCALE,
        number(entity.get("total_damage")) / _HP_SCALE,
        number(entity.get("passive")) / _SMALL_SCALE,
        number(entity.get("evoke")) / _SMALL_SCALE,
        number(entity.get("owner_index")) / _SMALL_SCALE,
        number(entity.get("bundle_index")) / _SMALL_SCALE,
        number(entity.get("card_index")) / _SMALL_SCALE,
        number(entity.get("target_index")) / _SMALL_SCALE,
        1.0 if entity.get("visited") else 0.0,
        1.0 if entity.get("current") else 0.0,
        number(entity.get("damage")) / _HP_SCALE,
        number(entity.get("from_col")) / _SMALL_SCALE,
        number(entity.get("from_row")) / _SMALL_SCALE,
        number(entity.get("option_index")) / _SMALL_SCALE,
        1.0 if entity.get("is_stocked") else 0.0,
        1.0 if entity.get("affordable") else 0.0,
    ))


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
        entries: dict[str, dict[str, int]] = {
            kind: {} for kind in ENTITY_KINDS if kind not in _VOCAB_KIND_ALIASES
        }
        next_index = 1
        for state in states:
            for entity in normalize_state(state).entities:
                kind = str(entity.get("entity_type"))
                kind = _VOCAB_KIND_ALIASES.get(kind, kind)
                key = entity_key(entity)
                if kind in entries and key != "UNK" and key not in entries[kind]:
                    entries[kind][key] = next_index
                    next_index += 1
        return cls(entries)

    def index(self, kind: str, key: str) -> int:
        kind = _VOCAB_KIND_ALIASES.get(kind, kind)
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


# candidate action -> (entity kind, argument holding the entity's index)
_CANDIDATE_ENTITY_REFS: Mapping[str, tuple[str, str]] = {
    "play_card": ("card", "card_index"),
    "select_card_reward": ("card", "card_index"),
    "buy_card": ("card", "card_index"),
    "use_potion": ("potion", "potion_index"),
    "buy_potion": ("potion", "potion_index"),
    "buy_relic": ("relic", "relic_index"),
    "choose_option": ("option", "option_index"),
    "select_bundle": ("choice", "bundle_index"),
}

MAX_CANDIDATE_BINDINGS = 16


def _find_entity(observation: NormalizedObservation, kind: str, field: str, wanted: Any) -> int:
    for row, entity in enumerate(observation.entities):
        if entity.get("entity_type") == kind and entity.get(field) == wanted:
            return row
    return -1


def _append_card_facts(bound: list[int], observation: NormalizedObservation, card_index: Any) -> None:
    for row, entity in enumerate(observation.entities):
        if (entity.get("entity_type") in {
                "card_stat", "enchantment", "affliction",
            } and entity.get("card_index") == card_index
                and entity.get("card_source") != "deck"):
            bound.append(row)


def candidate_entity_bindings(observation: NormalizedObservation, candidates) -> list[list[int]]:
    """All semantic entities referenced by every candidate.

    A single slot was enough for "play this card" but not for "play this card on
    that enemy", a multi-card selection, or a bundle containing several cards.
    The model pools these bindings per candidate.
    """
    rows: list[list[int]] = []
    for candidate in candidates:
        args = candidate.args or {}
        bound: list[int] = []
        ref = _CANDIDATE_ENTITY_REFS.get(candidate.action)
        if ref is not None:
            kind, arg = ref
            slot = _find_entity(observation, kind, "index", args.get(arg))
            if slot >= 0:
                bound.append(slot)
        elif candidate.action == "select_map_node":
            for row, entity in enumerate(observation.entities):
                if (entity.get("entity_type") == "choice"
                        and entity.get("col") == args.get("col")
                        and entity.get("row") == args.get("row")):
                    bound.append(row)
                    break
        elif candidate.action == "select_cards":
            for value in str(args.get("indices", "")).split(","):
                if not value.strip():
                    continue
                try:
                    wanted = int(value)
                except ValueError:
                    continue
                slot = _find_entity(observation, "card", "index", wanted)
                if slot >= 0:
                    bound.append(slot)
                    _append_card_facts(bound, observation, wanted)

        # Attach facts that belong to the selected object, rather than asking
        # attention to infer ownership from a small numeric index alone.
        card_index = args.get("card_index")
        if card_index is not None:
            _append_card_facts(bound, observation, card_index)

        if candidate.action == "choose_option":
            for row, entity in enumerate(observation.entities):
                if (entity.get("entity_type") == "event_var"
                        and entity.get("option_index") == args.get("option_index")):
                    bound.append(row)

        if candidate.action in {"play_card", "use_potion"} and "target_index" in args:
            target = _find_entity(observation, "enemy", "index", args["target_index"])
            if target >= 0:
                bound.append(target)
            if candidate.action == "play_card":
                for row, entity in enumerate(observation.entities):
                    if (entity.get("entity_type") == "card_target"
                            and entity.get("card_index") == args.get("card_index")
                            and entity.get("card_source") == "hand"
                            and entity.get("target_index") == args.get("target_index")):
                        bound.append(row)
                        break

        if candidate.action == "select_bundle":
            for row, entity in enumerate(observation.entities):
                if (entity.get("entity_type") == "bundle_card"
                        and entity.get("bundle_index") == args.get("bundle_index")):
                    bound.append(row)

        # Stable width keeps batching simple and makes an oversized new protocol
        # shape fail visibly rather than silently truncating arbitrary content.
        if len(bound) > MAX_CANDIDATE_BINDINGS:
            raise ValueError(f"candidate {candidate.action} references {len(bound)} entities")
        rows.append(bound + [-1] * (MAX_CANDIDATE_BINDINGS - len(bound)))
    return rows


def candidate_entity_slots(observation: NormalizedObservation, candidates) -> list[int]:
    """Entity row referenced by each candidate, -1 when there is none.

    The pointer head gathers the referenced entity's transformer output, so
    "take THIS card" is tied to that card's embedding instead of a bare index
    scalar the model would have to correlate with entity numerics on its own.
    """
    return [bindings[0] for bindings in candidate_entity_bindings(observation, candidates)]


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
