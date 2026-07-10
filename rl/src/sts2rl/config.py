from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path


@dataclass(frozen=True)
class ExperimentConfig:
    gamma: float = .999
    gae_lambda: float = .95
    ppo_clip: float = .2
    learning_rate: float = 3e-4
    batch_size: int = 256
    seed: int = 0
    character: str = "Ironclad"
    ascension: int = 0

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2) + "\n", encoding="utf-8")
