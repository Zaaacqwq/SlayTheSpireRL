from __future__ import annotations

from pathlib import Path


class ExperimentLogger:
    def __init__(self, log_dir: Path):
        self._writer = None
        try:
            from torch.utils.tensorboard import SummaryWriter
            self._writer = SummaryWriter(str(log_dir))
        except (ImportError, ModuleNotFoundError):
            log_dir.mkdir(parents=True, exist_ok=True)
            self._fallback = log_dir / "scalars.jsonl"

    def scalar(self, name: str, value: float, step: int) -> None:
        if self._writer: self._writer.add_scalar(name, value, step)
        else:
            with self._fallback.open("a", encoding="utf-8") as f: f.write(f'{{"name":"{name}","value":{value},"step":{step}}}\n')

    def close(self) -> None:
        if self._writer: self._writer.close()
