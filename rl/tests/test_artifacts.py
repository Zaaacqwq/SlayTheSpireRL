from __future__ import annotations

import json
from pathlib import Path

import pyarrow.parquet as pq

from sts2rl.artifacts import EpisodeArtifactWriter, IncrementalHistoryWriter, episode_rows
from sts2rl.ppo import EpisodeRecord, StoredStep
from sts2rl.protocol import ActionCandidate


def sample_record() -> EpisodeRecord:
    state = {
        "decision": "combat_play",
        "context": {"act": 1, "floor": 3, "room_type": "Monster"},
        "player": {"name": "Ironclad", "hp": 70, "max_hp": 80, "deck": []},
        "hand": [{"index": 0, "name": "Strike", "can_play": True}],
        "enemies": [{"index": 0, "name": "Jaw Worm", "hp": 40, "max_hp": 40}],
    }
    step = StoredStep(state, (ActionCandidate("play_card", {"card_index": 0, "target_index": 0}),),
                      0, -0.2, 0.4, 1.0, None)
    return EpisodeRecord("seed/unsafe", [step], True, False, final_floor=3)


def test_episode_rows_include_policy_and_action() -> None:
    row = episode_rows(sample_record())[0]
    assert row["action"]["action"] == "play_card"
    assert row["legal_actions"] == [row["action"]]
    assert row["logp"] == -0.2
    assert row["value"] == 0.4


def test_episode_writer_uses_compressed_parquet_and_manifest(tmp_path: Path) -> None:
    entry = EpisodeArtifactWriter(tmp_path).write(
        sample_record(), iteration=7, stage="act1", split="train", character="Ironclad",
    )
    target = tmp_path / entry["path"]
    assert target.exists()
    assert pq.ParquetFile(target).metadata.num_rows == 1
    assert not list(target.parent.glob("*.tmp"))
    manifest = [json.loads(line) for line in (tmp_path / "episodes/manifest.jsonl").read_text().splitlines()]
    assert manifest[0]["episode_id"] == "seed/unsafe"
    assert manifest[0]["total_reward"] == 1.0


def test_incremental_history_appends_without_rewriting(tmp_path: Path) -> None:
    writer = IncrementalHistoryWriter(tmp_path / "history.jsonl")
    writer.append({"iteration": 0, "loss": 1.0})
    writer.append({"iteration": 1, "loss": 0.5})
    assert [row["iteration"] for row in map(json.loads, writer.path.read_text().splitlines())] == [0, 1]
