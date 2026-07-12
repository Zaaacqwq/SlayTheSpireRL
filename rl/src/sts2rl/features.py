from __future__ import annotations

from typing import Any, Mapping, Sequence

import torch
from torch import Tensor

from .observation import GLOBAL_FEATURE_DIM, normalize_state
from .protocol import ActionCandidate, ProtocolError

# Every action name ``legal_actions`` can emit. A name outside this tuple means the
# protocol grew an action and the encoder was not updated, which would silently map
# two distinct actions onto the same features -- fail loudly instead.
ACTION_TYPES: tuple[str, ...] = (
    "play_card", "end_turn", "use_potion", "select_card_reward", "skip_card_reward",
    "select_cards", "skip_select", "select_bundle", "choose_option", "select_map_node",
    "remove_card", "leave_room",
)
ACTION_INDEX: Mapping[str, int] = {name: index for index, name in enumerate(ACTION_TYPES)}

# one-hot(action) + card_index + target_index + option_index + argument arity
CANDIDATE_FEATURE_DIM = len(ACTION_TYPES) + 4

_INDEX_SCALE = 10.0


def _slot(args: Mapping[str, Any], *names: str) -> float:
    for name in names:
        if name in args:
            try:
                return float(args[name]) / _INDEX_SCALE
            except (TypeError, ValueError):
                return -1.0 / _INDEX_SCALE
    return -1.0 / _INDEX_SCALE


def encode_candidate(candidate: ActionCandidate) -> tuple[float, ...]:
    if candidate.action not in ACTION_INDEX:
        raise ProtocolError(f"unknown action for feature encoding: {candidate.action!r}")
    one_hot = [0.0] * len(ACTION_TYPES)
    one_hot[ACTION_INDEX[candidate.action]] = 1.0
    args = candidate.args or {}
    return (
        *one_hot,
        _slot(args, "card_index", "indices"),
        _slot(args, "target_index"),
        _slot(args, "option_index", "bundle_index", "node_index"),
        float(len(args)),
    )


def encode_candidates(candidates: Sequence[ActionCandidate]) -> Tensor:
    if not candidates:
        raise ProtocolError("cannot encode an empty candidate list")
    return torch.tensor([encode_candidate(c) for c in candidates], dtype=torch.float32)


def encode_global(state: Mapping[str, Any]) -> Tensor:
    return torch.tensor(normalize_state(state).global_features, dtype=torch.float32)


def encode_batch(samples: Sequence[tuple[Mapping[str, Any], Sequence[ActionCandidate], int]]) -> dict[str, Tensor]:
    """Pad variable-length candidate lists into a rectangular batch.

    Padding rows are masked out; ``CandidatePolicy`` fills masked logits with the
    dtype minimum, so padded slots cannot be selected or contribute to the loss.
    """
    if not samples:
        raise ProtocolError("cannot encode an empty batch")
    width = max(len(candidates) for _, candidates, _ in samples)
    globals_, candidates_, masks, targets = [], [], [], []
    for state, candidates, chosen in samples:
        if not 0 <= chosen < len(candidates):
            raise ProtocolError(f"chosen index {chosen} outside candidate list of size {len(candidates)}")
        encoded = encode_candidates(candidates)
        padding = width - encoded.shape[0]
        globals_.append(encode_global(state))
        candidates_.append(torch.nn.functional.pad(encoded, (0, 0, 0, padding)))
        masks.append(torch.tensor([True] * encoded.shape[0] + [False] * padding))
        targets.append(chosen)
    return {
        "global": torch.stack(globals_),
        "candidates": torch.stack(candidates_),
        "mask": torch.stack(masks),
        "targets": torch.tensor(targets, dtype=torch.long),
    }
