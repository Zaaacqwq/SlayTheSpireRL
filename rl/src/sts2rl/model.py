from __future__ import annotations

import torch
from torch import nn, Tensor

from .entities import ENTITY_KINDS, ENTITY_NUMERIC_DIM, PHASES
from .features import CANDIDATE_FEATURE_DIM
from .observation import GLOBAL_FEATURE_DIM


class CandidatePolicy(nn.Module):
    """Small masked candidate scorer; variable candidate count is supported."""
    def __init__(self, global_dim: int = GLOBAL_FEATURE_DIM, hidden: int = 128):
        super().__init__()
        self.encoder = nn.Sequential(nn.Linear(global_dim, hidden), nn.Tanh(), nn.Linear(hidden, hidden), nn.Tanh())
        self.candidate = nn.LazyLinear(hidden)
        self.pointer = nn.Linear(hidden, 1)
        self.value = nn.Linear(hidden, 1)

    def forward(self, global_features: Tensor, candidate_features: Tensor, mask: Tensor | None = None) -> tuple[Tensor, Tensor]:
        h = self.encoder(global_features)
        c = self.candidate(candidate_features)
        logits = self.pointer(torch.tanh(c + h.unsqueeze(-2))).squeeze(-1)
        if mask is not None: logits = logits.masked_fill(~mask, torch.finfo(logits.dtype).min)
        return logits, self.value(h).squeeze(-1)


class RecurrentCandidatePolicy(CandidatePolicy):
    def __init__(self, global_dim: int = GLOBAL_FEATURE_DIM, hidden: int = 128):
        super().__init__(global_dim, hidden)
        self.history = nn.GRU(hidden, hidden, batch_first=True)

    def forward_sequence(self, global_sequence: Tensor, candidate_features: Tensor, mask: Tensor | None = None, hidden: Tensor | None = None):
        encoded = self.encoder(global_sequence)
        encoded, hidden = self.history(encoded, hidden)
        candidates = self.candidate(candidate_features)
        logits = self.pointer(torch.tanh(candidates + encoded.unsqueeze(-2))).squeeze(-1)
        if mask is not None: logits = logits.masked_fill(~mask, torch.finfo(logits.dtype).min)
        return logits, self.value(encoded).squeeze(-1), hidden


class EntityTransformerPolicy(nn.Module):
    """Roadmap architecture core: entity transformer + phase embedding + pointer head.

    Consumes ``entities.encode_entity_batch`` output. Padded entity slots are
    excluded via attention key padding; padded candidates score dtype-min.
    """

    def __init__(self, vocab_size: int, *, global_dim: int = GLOBAL_FEATURE_DIM, candidate_dim: int = CANDIDATE_FEATURE_DIM,
                 hidden: int = 128, heads: int = 4, layers: int = 2):
        super().__init__()
        self.type_embed = nn.Embedding(len(ENTITY_KINDS) + 1, hidden)
        self.id_embed = nn.Embedding(vocab_size, hidden)
        self.numeric = nn.Linear(ENTITY_NUMERIC_DIM, hidden)
        self.phase_embed = nn.Embedding(len(PHASES), hidden)
        self.global_proj = nn.Linear(global_dim, hidden)
        layer = nn.TransformerEncoderLayer(hidden, heads, hidden * 4, dropout=0.0, batch_first=True)
        self.encoder = nn.TransformerEncoder(layer, layers)
        self.candidate = nn.Linear(candidate_dim, hidden)
        self.pointer = nn.Linear(hidden, 1)
        self.value = nn.Linear(hidden, 1)

    def encode_context(self, global_features: Tensor, entities: dict[str, Tensor]) -> tuple[Tensor, Tensor]:
        tokens = (
            self.type_embed(entities["entity_type"])
            + self.id_embed(entities["entity_id"])
            + self.numeric(entities["entity_numeric"])
        )
        entity_mask = entities["entity_mask"]
        encoded = self.encoder(tokens, src_key_padding_mask=~entity_mask)
        weights = entity_mask.unsqueeze(-1).to(encoded.dtype)
        pooled = (encoded * weights).sum(-2) / weights.sum(-2).clamp_min(1.0)
        context = torch.tanh(
            pooled + self.global_proj(global_features) + self.phase_embed(entities["phase"])
        )
        return context, encoded

    def heads(self, context: Tensor, encoded_entities: Tensor, candidate_features: Tensor,
              candidate_mask: Tensor | None = None,
              candidate_slots: Tensor | None = None) -> tuple[Tensor, Tensor]:
        candidates = self.candidate(candidate_features)
        if candidate_slots is not None:
            # "Take THIS card" points at that card's transformer output; a bare
            # index scalar in the features is not reliably learnable.
            if candidate_slots.ndim == 2:
                candidate_slots = candidate_slots.unsqueeze(-1)
            safe = candidate_slots.clamp_min(0)
            expanded = encoded_entities.unsqueeze(1).expand(
                -1, candidate_slots.shape[1], -1, -1,
            )
            gathered = expanded.gather(
                2, safe.unsqueeze(-1).expand(-1, -1, -1, encoded_entities.shape[-1]),
            )
            binding_mask = (candidate_slots >= 0).unsqueeze(-1).to(gathered.dtype)
            bound = (gathered * binding_mask).sum(2) / binding_mask.sum(2).clamp_min(1.0)
            candidates = candidates + bound
        logits = self.pointer(torch.tanh(candidates + context.unsqueeze(-2))).squeeze(-1)
        if candidate_mask is not None:
            logits = logits.masked_fill(~candidate_mask, torch.finfo(logits.dtype).min)
        return logits, self.value(context).squeeze(-1)

    def forward(self, global_features: Tensor, entities: dict[str, Tensor],
                candidate_features: Tensor, candidate_mask: Tensor | None = None,
                candidate_slots: Tensor | None = None) -> tuple[Tensor, Tensor]:
        context, encoded = self.encode_context(global_features, entities)
        return self.heads(context, encoded, candidate_features, candidate_mask, candidate_slots)


class EntityRecurrentPolicy(EntityTransformerPolicy):
    """Adds GRU history over decision steps.

    The recurrent state is treated as data during PPO updates (stored detached,
    no backpropagation through time) — gradients flow through the current
    step's context and the GRU cell only.
    """

    def __init__(self, vocab_size: int, *, global_dim: int = GLOBAL_FEATURE_DIM, candidate_dim: int = CANDIDATE_FEATURE_DIM,
                 hidden: int = 128, heads: int = 4, layers: int = 2):
        super().__init__(vocab_size, global_dim=global_dim, candidate_dim=candidate_dim,
                         hidden=hidden, heads=heads, layers=layers)
        self.history = nn.GRUCell(hidden, hidden)
        self.hidden_size = hidden

    def initial_hidden(self, batch: int = 1) -> Tensor:
        return torch.zeros(batch, self.hidden_size)

    def forward(self, global_features: Tensor, entities: dict[str, Tensor],  # type: ignore[override]
                candidate_features: Tensor, candidate_mask: Tensor | None = None,
                candidate_slots: Tensor | None = None,
                hidden: Tensor | None = None) -> tuple[Tensor, Tensor, Tensor]:
        context, encoded = self.encode_context(global_features, entities)
        if hidden is None:
            hidden = self.initial_hidden(context.shape[0]).to(context.device)
        new_hidden = self.history(context, hidden)
        logits, value = self.heads(new_hidden, encoded, candidate_features, candidate_mask, candidate_slots)
        return logits, value, new_hidden
