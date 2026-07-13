from __future__ import annotations

from pathlib import Path
import torch


def save_checkpoint(path: Path, model, optimizer, *, step: int, config: dict, seed_hash: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save({"model": model.state_dict(), "optimizer": optimizer.state_dict(), "step": step, "config": config, "seed_hash": seed_hash}, path)


def _migrate_state_dict(model, state: dict) -> list[str]:
    """Grow embedding tables saved before new entity kinds were appended.

    New rows are zero-initialised: a zero type embedding contributes nothing
    until training moves it, so a migrated model behaves exactly like the old
    one on inputs that never mention the new kinds.
    """
    migrated: list[str] = []
    current = model.state_dict()
    for key, saved in state.items():
        want = current.get(key)
        if want is None or torch.nn.parameter.is_lazy(want):
            continue
        if (saved.shape == want.shape or saved.ndim != want.ndim
                or saved.ndim < 1 or saved.shape[1:] != want.shape[1:]
                or saved.shape[0] > want.shape[0] or not key.endswith("embed.weight")):
            continue
        grown = torch.zeros_like(want)
        grown[: saved.shape[0]] = saved
        state[key] = grown
        migrated.append(key)
    return migrated


def load_checkpoint(path: Path, model, optimizer=None) -> dict:
    payload = torch.load(path, map_location="cpu")
    migrated = _migrate_state_dict(model, payload["model"])
    model.load_state_dict(payload["model"])
    payload["migrated_keys"] = migrated
    if optimizer is not None:
        if migrated:
            # Stale Adam moments do not match the grown parameters; the caller
            # continues with a fresh optimizer state.
            payload["optimizer_skipped"] = True
        else:
            optimizer.load_state_dict(payload["optimizer"])
    return payload
