from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any, Mapping


@dataclass
class Transition:
    episode_id: str
    step: int
    state: Mapping[str, Any]
    normalized: Mapping[str, Any]
    legal_actions: list[Mapping[str, Any]]
    action: Mapping[str, Any]
    reward: float
    terminated: bool
    outcome: str | None
    engine_version: str | None


class TrajectoryWriter:
    """Writes JSONL now; uses pyarrow when installed for Parquet export."""
    def __init__(self, path: Path): self.path = path
    def write(self, transitions: list[Transition]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        rows = [asdict(t) for t in transitions]
        if self.path.suffix == ".parquet":
            try:
                import pyarrow as pa, pyarrow.parquet as pq
                pq.write_table(pa.Table.from_pylist(rows), self.path); return
            except ImportError: pass
        with self.path.with_suffix(self.path.suffix + ".jsonl").open("w", encoding="utf-8") as f:
            for row in rows: f.write(json.dumps(row, ensure_ascii=False) + "\n")
