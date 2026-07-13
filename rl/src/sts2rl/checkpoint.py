from __future__ import annotations

from pathlib import Path
import torch


def save_checkpoint(path: Path, model, optimizer, *, step: int, config: dict, seed_hash: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save({"model": model.state_dict(), "optimizer": optimizer.state_dict(), "step": step, "config": config, "seed_hash": seed_hash}, path)


def _grown(key, saved, want):
    """Zero-padded copy of ``saved`` matching ``want``, or None if not growable.

    Embedding tables grow rows (new entity kinds appended last); the entity
    numeric projection grows input columns (new numeric features appended
    last). Zero padding neutralises exactly the new channel: old states, where
    the new features are all zero and the new kinds never occur, produce
    bit-identical outputs.
    """
    if saved.shape == want.shape or saved.ndim != want.ndim or saved.ndim < 1:
        return None
    if (key.endswith("embed.weight")
            and saved.shape[1:] == want.shape[1:] and saved.shape[0] < want.shape[0]):
        grown = torch.zeros_like(want)
        grown[: saved.shape[0]] = saved
        return grown
    if (key.endswith("numeric.weight") and saved.ndim == 2
            and saved.shape[0] == want.shape[0] and saved.shape[1] < want.shape[1]):
        grown = torch.zeros_like(want)
        grown[:, : saved.shape[1]] = saved
        return grown
    return None


def _migrate_state_dict(model, state: dict) -> list[str]:
    """Grow tensors saved before new entity kinds/features were appended.

    A migrated model is identical on states that contain none of the new
    content, but sees a distribution shift on states that do — resuming
    across an observation change is a retraining event, which is also why
    the stale Adam state is dropped.
    """
    migrated: list[str] = []
    current = model.state_dict()
    for key, saved in state.items():
        want = current.get(key)
        if want is None or torch.nn.parameter.is_lazy(want):
            continue
        grown = _grown(key, saved, want)
        if grown is not None:
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
