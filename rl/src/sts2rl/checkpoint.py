from __future__ import annotations

from pathlib import Path
import torch


def save_checkpoint(path: Path, model, optimizer, *, step: int, config: dict, seed_hash: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save({"model": model.state_dict(), "optimizer": optimizer.state_dict(), "step": step, "config": config, "seed_hash": seed_hash}, path)


def load_checkpoint(path: Path, model, optimizer=None) -> dict:
    payload = torch.load(path, map_location="cpu")
    model.load_state_dict(payload["model"])
    if optimizer is not None: optimizer.load_state_dict(payload["optimizer"])
    return payload
