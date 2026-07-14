"""Audit recorded episodes for policy/action visibility before PPO updates.

Reads the Parquet artifacts produced by ``m2_train.py`` and writes one stable
``visibility_audit.json`` report. Exit status 2 means the strict contract found
unknown fields/entities, pointer misses, candidate collisions, or non-finite
features.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

import pyarrow.parquet as pq

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "rl" / "src"))

from sts2rl.entities import EntityVocab
from sts2rl.ppo import EpisodeRecord, StoredStep
from sts2rl.protocol import ActionCandidate
from sts2rl.visibility import visibility_audit


def _without_nulls(value: Any) -> Any:
    """Undo PyArrow's union-of-struct-fields null padding."""
    if isinstance(value, dict):
        return {key: _without_nulls(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_without_nulls(item) for item in value]
    return value


def _record(path: Path) -> EpisodeRecord:
    rows = pq.read_table(path).to_pylist()
    steps: list[StoredStep] = []
    for row in rows:
        candidates = []
        for command in row["legal_actions"]:
            command = _without_nulls(command)
            candidates.append(ActionCandidate(
                str(command["action"]), dict(command.get("args") or {}),
            ))
        steps.append(StoredStep(
            _without_nulls(row["state"]), tuple(candidates), int(row["action_index"]),
            float(row.get("logp") or 0.0), float(row.get("value") or 0.0),
            float(row.get("reward") or 0.0), None,
        ))
    outcome = rows[-1].get("outcome") if rows else None
    return EpisodeRecord(path.stem, steps, outcome, False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dirs", type=Path, nargs="+")
    parser.add_argument("--split", choices=("train", "dev", "replay"), default=None)
    parser.add_argument("--limit", type=int, default=None, help="newest N episode files")
    parser.add_argument(
        "--vocab", type=Path, default=REPO_ROOT / "rl" / "schema" / "m2_vocab.json",
    )
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--allow-violations", action="store_true")
    args = parser.parse_args()

    paths = []
    for run_dir in args.run_dirs:
        episode_root = run_dir / "episodes"
        paths.extend(
            sorted((episode_root / args.split).glob("*.parquet")) if args.split
            else sorted(episode_root.glob("*/*.parquet"))
        )
    if args.limit is not None:
        paths = paths[-args.limit:]
    if not paths:
        raise SystemExit("no episode artifacts found under the requested runs")

    report = visibility_audit([_record(path) for path in paths], EntityVocab.load(args.vocab))
    report = {
        "run_dirs": [str(path.resolve()) for path in args.run_dirs],
        "split": args.split,
        "files": len(paths),
        **report,
    }
    output = args.output or args.run_dirs[0] / "visibility_audit.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), **report}, ensure_ascii=False))
    return 0 if args.allow_violations or not report["violations"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
