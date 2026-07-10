from __future__ import annotations

import torch
from torch import Tensor


def behavior_cloning_loss(logits: Tensor, target: Tensor, mask: Tensor | None = None) -> Tensor:
    """Cross entropy over candidate scores; target indexes current candidate list."""
    loss = torch.nn.functional.cross_entropy(logits, target, reduction="none")
    return (loss * mask).sum() / mask.sum().clamp_min(1) if mask is not None else loss.mean()


def generalized_advantage_estimate(rewards: Tensor, values: Tensor, dones: Tensor, gamma: float = .999, lam: float = .95, next_value: Tensor | None = None) -> tuple[Tensor, Tensor]:
    next_value = values[-1].detach() if next_value is None else next_value
    advantages = torch.zeros_like(rewards)
    gae = torch.zeros((), dtype=rewards.dtype, device=rewards.device)
    for t in reversed(range(len(rewards))):
        nv = next_value if t == len(rewards) - 1 else values[t + 1]
        delta = rewards[t] + gamma * nv * (1 - dones[t]) - values[t]
        gae = delta + gamma * lam * (1 - dones[t]) * gae
        advantages[t] = gae
    return advantages, advantages + values


def ppo_clipped_loss(old_logp: Tensor, new_logp: Tensor, advantages: Tensor, clip: float = .2) -> Tensor:
    ratio = (new_logp - old_logp).exp()
    return -torch.minimum(ratio * advantages, ratio.clamp(1 - clip, 1 + clip) * advantages).mean()
