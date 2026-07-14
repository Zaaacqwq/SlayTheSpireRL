"""Fail-fast visibility contract for the policy input and action space."""
from __future__ import annotations

from collections import Counter
import math
from typing import Sequence

from .entities import (
    EntityVocab, _VOCAB_KIND_ALIASES, candidate_entity_bindings, entity_key,
)
from .features import ACTION_TYPES, encode_candidate
from .observation import normalize_state
from .ppo import EpisodeRecord


ENTITY_ACTIONS = frozenset({
    "play_card", "use_potion", "select_card_reward", "select_cards",
    "select_bundle", "choose_option", "select_map_node", "buy_card",
    "buy_relic", "buy_potion",
})


def visibility_audit(records: Sequence[EpisodeRecord], vocab: EntityVocab) -> dict:
    offered: Counter[str] = Counter({name: 0 for name in ACTION_TYPES})
    chosen: Counter[str] = Counter({name: 0 for name in ACTION_TYPES})
    pointer_misses: Counter[str] = Counter()
    unknown_fields: Counter[str] = Counter()
    unknown_entities: Counter[str] = Counter()
    collisions = 0
    nonfinite_features = 0
    decisions = 0

    for record in records:
        for step in record.steps:
            decisions += 1
            observation = normalize_state(step.raw_state)
            unknown_fields.update(observation.warnings)
            for entity in observation.entities:
                kind = str(entity.get("entity_type"))
                vocab_kind = _VOCAB_KIND_ALIASES.get(kind, kind)
                key = entity_key(entity)
                if key == "UNK" or key not in vocab.entries.get(vocab_kind, {}):
                    unknown_entities[f"{vocab_kind}:{key}"] += 1

            bindings = candidate_entity_bindings(observation, step.candidates)
            seen: set[tuple[tuple[float, ...], tuple[int, ...]]] = set()
            for candidate, bound in zip(step.candidates, bindings):
                offered[candidate.action] += 1
                encoded = encode_candidate(candidate)
                if not all(math.isfinite(value) for value in encoded):
                    nonfinite_features += 1
                semantic = (encoded, tuple(bound))
                if semantic in seen:
                    collisions += 1
                seen.add(semantic)
                if candidate.action in ENTITY_ACTIONS and all(slot < 0 for slot in bound):
                    pointer_misses[candidate.action] += 1
            chosen[step.candidates[step.index].action] += 1
            if not all(math.isfinite(value) for value in observation.global_features):
                nonfinite_features += 1

    violations: list[str] = []
    if collisions:
        violations.append(f"candidate_collisions:{collisions}")
    if pointer_misses:
        violations.append("pointer_misses:" + ",".join(
            f"{name}={count}" for name, count in sorted(pointer_misses.items())
        ))
    if unknown_fields:
        violations.append(f"unknown_state_fields:{sum(unknown_fields.values())}")
    if unknown_entities:
        violations.append(f"unknown_entities:{sum(unknown_entities.values())}")
    if nonfinite_features:
        violations.append(f"nonfinite_features:{nonfinite_features}")

    never_offered = [name for name in ACTION_TYPES if offered[name] == 0]
    never_chosen = [name for name in ACTION_TYPES if offered[name] > 0 and chosen[name] == 0]

    return {
        "decisions": decisions,
        "offered_actions": dict(offered),
        "chosen_actions": dict(chosen),
        "never_offered_actions": never_offered,
        "never_chosen_actions": never_chosen,
        "candidate_collisions": collisions,
        "pointer_misses": dict(pointer_misses),
        "unknown_state_fields": dict(unknown_fields),
        "unknown_entities": dict(unknown_entities.most_common(25)),
        "nonfinite_features": nonfinite_features,
        "violations": violations,
    }
