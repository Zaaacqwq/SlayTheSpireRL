"""Single-decision policy wrapper used by rollout workers and evaluators."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import torch
from torch import Tensor

from .entities import EntityVocab, candidate_entity_slots, encode_entity_batch
from .features import encode_candidates
from .model import EntityRecurrentPolicy, EntityTransformerPolicy
from .observation import normalize_state
from .protocol import ActionCandidate


@dataclass(frozen=True)
class AgentStep:
    index: int
    logp: float
    value: float
    hidden: tuple[float, ...] | None


class PolicyAgent:
    """Encodes one decision state and samples a legal candidate."""

    def __init__(self, model: EntityTransformerPolicy, vocab: EntityVocab):
        self.model = model
        self.vocab = vocab
        self.recurrent = isinstance(model, EntityRecurrentPolicy)
        self.device = next(model.parameters()).device

    def act(
        self,
        raw_state: Mapping[str, Any],
        candidates: Sequence[ActionCandidate],
        hidden: tuple[float, ...] | None = None,
        *,
        greedy: bool = False,
        generator: torch.Generator | None = None,
    ) -> AgentStep:
        observation = normalize_state(raw_state)
        entities = {k: v.to(self.device) for k, v in encode_entity_batch([observation], self.vocab).items()}
        candidate_features = encode_candidates(candidates).unsqueeze(0).to(self.device)
        slots = torch.tensor([candidate_entity_slots(observation, candidates)], dtype=torch.long).to(self.device)
        with torch.no_grad():
            if self.recurrent:
                hidden_tensor = (
                    torch.tensor([hidden], dtype=torch.float32).to(self.device)
                    if hidden is not None else None
                )
                logits, value, new_hidden = self.model(
                    entities_global(observation).to(self.device), entities, candidate_features,
                    candidate_slots=slots, hidden=hidden_tensor,
                )
                next_hidden: tuple[float, ...] | None = tuple(new_hidden[0].cpu().tolist())
            else:
                logits, value = self.model(
                    entities_global(observation).to(self.device), entities, candidate_features,
                    candidate_slots=slots,
                )
                next_hidden = None
            probs = torch.softmax(logits[0], dim=-1)
            if greedy:
                index = int(torch.argmax(probs))
            else:
                index = int(torch.multinomial(probs, 1, generator=generator))
            logp = float(torch.log_softmax(logits[0], dim=-1)[index])
        return AgentStep(index, logp, float(value[0]), next_hidden)


def entities_global(observation) -> Tensor:
    return torch.tensor([observation.global_features], dtype=torch.float32)
