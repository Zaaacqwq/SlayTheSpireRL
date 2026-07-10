from __future__ import annotations

import hashlib
from typing import Literal

Split = Literal["train", "development", "test"]


def split_seed(seed: str) -> Split:
    """Stable 80/10/10 split; namespaces prevent accidental cross-use."""
    bucket = int.from_bytes(hashlib.sha256(("sts2-rl-v2:" + seed).encode()).digest()[:8], "big") % 100
    return "train" if bucket < 80 else "development" if bucket < 90 else "test"


def seed_hash(seeds: list[str]) -> str:
    return hashlib.sha256("\n".join(sorted(seeds)).encode()).hexdigest()
