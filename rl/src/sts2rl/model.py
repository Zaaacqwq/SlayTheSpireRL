from __future__ import annotations

import torch
from torch import nn, Tensor


class CandidatePolicy(nn.Module):
    """Small masked candidate scorer; variable candidate count is supported."""
    def __init__(self, global_dim: int = 8, hidden: int = 128):
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
