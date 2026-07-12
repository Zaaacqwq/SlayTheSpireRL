"""Durable, dashboard-friendly artifacts for PPO runs."""
from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
import re
from typing import Any, Iterable

from .ppo import EpisodeRecord


_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")


def _command(candidate: Any) -> dict[str, Any]:
    command = candidate.command()
    return dict(command) if not isinstance(command, dict) else command


def episode_rows(record: EpisodeRecord) -> list[dict[str, Any]]:
    """Convert the in-memory PPO record without changing training tensors."""
    rows: list[dict[str, Any]] = []
    for step_number, step in enumerate(record.steps):
        legal_actions = [_command(candidate) for candidate in step.candidates]
        action = legal_actions[step.index]
        rows.append({
            "episode_id": record.seed,
            "step": step_number,
            "state": dict(step.raw_state),
            "legal_actions": legal_actions,
            "action": action,
            "action_index": step.index,
            "reward": step.reward,
            "logp": step.logp,
            "value": step.value,
            "terminated": step_number == len(record.steps) - 1 and not record.truncated,
            "outcome": record.outcome,
        })
    return rows


class EpisodeArtifactWriter:
    """Writes one compressed Parquet per episode plus an append-only manifest."""

    def __init__(self, run_dir: Path):
        self.run_dir = run_dir
        self.manifest_path = run_dir / "episodes" / "manifest.jsonl"

    def write_many(
        self,
        records: Iterable[EpisodeRecord],
        *,
        iteration: int,
        stage: str,
        split: str,
        character: str,
    ) -> list[dict[str, Any]]:
        entries = []
        for record in records:
            entries.append(self.write(
                record, iteration=iteration, stage=stage, split=split,
                character=character,
            ))
        return entries

    def write(
        self,
        record: EpisodeRecord,
        *,
        iteration: int,
        stage: str,
        split: str,
        character: str,
    ) -> dict[str, Any]:
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError as exc:  # pragma: no cover - environment configuration
            raise RuntimeError("full episode recording requires the RL export dependency (pyarrow)") from exc

        safe_seed = _SAFE_NAME.sub("_", record.seed)
        relative = Path("episodes") / split / f"{iteration:05d}_{safe_seed}.parquet"
        target = self.run_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_suffix(target.suffix + f".{os.getpid()}.tmp")
        rows = episode_rows(record)
        # Parquet cannot infer a schema from a completely empty episode. Keep the
        # failure visible in the manifest without manufacturing a fake step.
        if rows:
            table = pa.Table.from_pylist(rows)
            pq.write_table(table, temp, compression="zstd")
            os.replace(temp, target)

        total_reward = sum(float(step.reward) for step in record.steps)
        entry = {
            "episode_id": record.seed,
            "path": str(relative) if rows else None,
            "iteration": iteration,
            "stage": stage,
            "split": split,
            "character": character,
            "outcome": record.outcome,
            "total_reward": total_reward,
            "final_floor": record.final_floor,
            "steps": len(record.steps),
            "truncated": record.truncated,
            "error": record.error,
        }
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        with self.manifest_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return entry


class IncrementalHistoryWriter:
    """Append metrics immediately so live readers never wait for shutdown."""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, row: dict[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
