from __future__ import annotations

import torch
from torch import Tensor
from .losses import behavior_cloning_loss, ppo_clipped_loss


def bc_update(model, optimizer, global_features: Tensor, candidate_features: Tensor, targets: Tensor, mask: Tensor) -> float:
    model.train(); logits, _ = model(global_features, candidate_features, mask)
    loss = behavior_cloning_loss(logits, targets)
    optimizer.zero_grad(); loss.backward(); optimizer.step()
    return float(loss.detach())


def ppo_update(model, optimizer, batch: dict[str, Tensor], clip: float = .2) -> dict[str, float]:
    model.train(); logits, values = model(batch["global"], batch["candidates"], batch.get("mask"))
    new_logp = torch.log_softmax(logits, -1).gather(-1, batch["actions"].unsqueeze(-1)).squeeze(-1)
    policy_loss = ppo_clipped_loss(batch["old_logp"], new_logp, batch["advantages"], clip)
    value_loss = torch.nn.functional.mse_loss(values, batch["returns"])
    loss = policy_loss + .5 * value_loss
    optimizer.zero_grad(); loss.backward(); optimizer.step()
    return {"loss": float(loss.detach()), "policy_loss": float(policy_loss.detach()), "value_loss": float(value_loss.detach())}
