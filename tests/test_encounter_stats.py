from __future__ import annotations

import json
from pathlib import Path

import tools.encounter_stats as encounters_module
from tools.encounter_stats import (
    RunCache,
    aggregate,
    encounter_stats,
    episode_combats,
    refresh,
)


def row(floor: int, room: str, *, enemies=None, hp: int = 80, act: int = 1,
        boss: dict | None = None, step: int = 0) -> dict:
    """One projected decision row, shaped exactly as the Parquet reader returns it."""
    context = {"act": act, "floor": floor, "room_type": room}
    if boss is not None:
        context["boss"] = boss
    return {"step": step, "context": context, "enemies": enemies, "hp": hp}


def enemy(name: str) -> dict:
    return {"name": name, "hp": 10, "max_hp": 10}


def test_leaving_a_combat_room_scores_the_fight_as_a_win() -> None:
    rows = [
        row(1, "Map"),
        row(2, "Monster", enemies=[enemy("Nibbit")], hp=80),
        row(2, "Monster", enemies=[enemy("Nibbit")], hp=72),
        row(2, "Monster", enemies=None, hp=78),  # post-combat card reward, +heal
        row(3, "Map", hp=78),
    ]

    combats = episode_combats(rows, outcome=False)

    assert [(c.kind, c.label, c.result) for c in combats] == [("monster", "Nibbit", "win")]
    # HP is sampled only while enemies are on the field, so Burning Blood's
    # end-of-combat heal cannot make the fight look cheaper than it was.
    assert (combats[0].hp_in, combats[0].hp_out) == (80, 72)


def test_the_fight_the_run_dies_inside_is_the_only_loss() -> None:
    rows = [
        row(2, "Monster", enemies=[enemy("Nibbit")], hp=80),
        row(3, "Map", hp=70),
        row(4, "Elite", enemies=[enemy("Byrdonis")], hp=70),
        row(4, "Elite", enemies=[enemy("Byrdonis")], hp=12),
    ]

    combats = episode_combats(rows, outcome=False)

    assert [(c.kind, c.result) for c in combats] == [("monster", "win"), ("elite", "loss")]


def test_dying_outside_combat_leaves_every_finished_fight_a_win() -> None:
    """An event can deal lethal damage. The elite before it was still beaten."""
    rows = [
        row(4, "Elite", enemies=[enemy("Byrdonis")], hp=40),
        row(5, "Event", hp=8),
    ]

    combats = episode_combats(rows, outcome=False)

    assert [c.result for c in combats] == ["win"]


def test_truncated_final_fight_is_unresolved_not_a_loss() -> None:
    rows = [row(15, "Boss", enemies=[enemy("Kin Priest")], boss={"name": "Kin Priest"}, hp=30)]

    combats = episode_combats(rows, outcome=None)

    assert [c.result for c in combats] == ["unresolved"]


def test_spawn_groups_are_counted_so_three_of_a_kind_stays_distinct() -> None:
    rows = [row(2, "Monster", enemies=[enemy("Inklet")] * 3)]

    combats = episode_combats(rows, outcome=True)

    assert combats[0].label == "Inklet x3"
    assert combats[0].enemies == ("Inklet",)


def test_a_boss_that_spawns_no_enemies_still_gets_a_row() -> None:
    """The KAISER_CRAB free win: zero enemies spawn, the run walks past the boss.

    Keying the fight on the room rather than on the enemy list is what keeps that
    engine bug visible as a 100%-win, zero-HP-lost boss instead of a silent gap.
    """
    rows = [
        row(15, "Boss", enemies=None, boss={"name": "Kaiser Crab"}, hp=60),
        row(15, "Boss", enemies=None, boss={"name": "Kaiser Crab"}, hp=60),
        row(16, "Map", hp=60),
    ]

    combats = episode_combats(rows, outcome=True)

    assert [(c.kind, c.label, c.result) for c in combats] == [("boss", "Kaiser Crab", "win")]
    assert combats[0].hp_in is None  # nothing was ever fought


def test_the_same_enemy_is_credited_across_every_group_it_appears_in() -> None:
    rows = [
        row(2, "Monster", enemies=[enemy("Flyconid"), enemy("Twig Slime")], hp=80),
        row(3, "Map", hp=60),
        row(4, "Monster", enemies=[enemy("Flyconid")], hp=60),
        row(4, "Monster", enemies=[enemy("Flyconid")], hp=4),
    ]
    cache = _cache_of([("episodes/train/a.parquet", 1, "train", episode_combats(rows, False))])

    payload = aggregate(cache)

    encounters = {item["label"]: item for item in payload["encounters"]}
    assert encounters["Flyconid + Twig Slime"]["win_rate"] == 1.0
    assert encounters["Flyconid"]["win_rate"] == 0.0
    enemies = {item["label"]: item for item in payload["enemies"]}
    assert enemies["Flyconid"]["fights"] == 2
    assert enemies["Flyconid"]["win_rate"] == 0.5
    assert enemies["Twig Slime"]["fights"] == 1


def _cache_of(episodes) -> RunCache:
    cache = RunCache(path=Path("unused"))
    for path, iteration, split, combats in episodes:
        cache.entries[path] = {
            "path": path, "iteration": iteration, "split": split,
            "combats": [combat.as_dict() for combat in combats],
        }
    return cache


def test_last_iterations_window_drops_the_early_policy() -> None:
    weak = episode_combats([row(15, "Boss", enemies=[enemy("Kin Priest")], hp=10)], outcome=False)
    strong = episode_combats([
        row(15, "Boss", enemies=[enemy("Kin Priest")], hp=60), row(16, "Map", hp=40),
    ], outcome=True)
    cache = _cache_of([
        ("a.parquet", 10, "dev", weak),
        ("b.parquet", 20, "dev", weak),
        ("c.parquet", 30, "dev", strong),
    ])

    assert aggregate(cache)["encounters"][0]["win_rate"] == round(1 / 3, 4)
    assert aggregate(cache, last_iterations=1)["encounters"][0]["win_rate"] == 1.0
    assert aggregate(cache, split="train")["encounters"] == []


def make_parquet(path: Path, rows: list[dict]) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), path)


def _episode_rows(enemy_name: str, hp: list[int]) -> list[dict]:
    """Full-fat episode rows, so the nested column projection is exercised."""
    return [
        {
            "episode_id": "seed-1", "step": index,
            "state": {
                "decision": "combat_play",
                "context": {"act": 1, "floor": 4.0, "room_type": "Elite",
                            "boss": {"id": "B", "name": "Boss"}},
                "player": {"name": "Ironclad", "hp": value, "max_hp": 80},
                "enemies": [{"index": 0, "id": f"MONSTER.{enemy_name}", "name": enemy_name,
                             "hp": 30, "max_hp": 30}],
            },
            "reward": 0.0, "terminated": False, "outcome": False,
        }
        for index, value in enumerate(hp)
    ]


def test_end_to_end_scan_caches_per_episode_and_survives_a_restart(tmp_path: Path, monkeypatch) -> None:
    run = tmp_path / "run"
    relative = "episodes/dev/00007_seed-1.parquet"
    make_parquet(run / relative, _episode_rows("Byrdonis", [80, 40, 6]))
    (run / "episodes" / "manifest.jsonl").write_text(json.dumps({
        "episode_id": "seed-1", "path": relative, "iteration": 7, "split": "dev",
        "stage": "act1", "outcome": False, "steps": 3, "error": None,
    }) + "\n", encoding="utf-8")

    payload = encounter_stats(run, blocking=True)

    assert payload["progress"] == {"scanned": 1, "total": 1, "building": False, "error": None}
    assert payload["by_kind"]["elite"] == [{
        "key": "elite/Byrdonis", "label": "Byrdonis", "kind": "elite", "fights": 1,
        "wins": 0, "losses": 1, "unresolved": 0, "win_rate": 0.0,
        "avg_hp_lost": 74.0, "avg_hp_in": 80.0, "acts": [1],
    }]
    assert payload["scope"]["fights"] == 1

    # A fresh process re-reads the cache file instead of the Parquet: an episode
    # is only ever parsed once, which is what makes a 6000-file run affordable.
    monkeypatch.setattr(encounters_module, "_CACHES", {})
    monkeypatch.setattr(encounters_module, "_MANIFEST_CACHE", {})
    monkeypatch.setattr(encounters_module, "read_episode_combats", _explode)
    reloaded = refresh(run, blocking=True)
    assert reloaded.entries[relative]["combats"][0]["label"] == "Byrdonis"
    assert reloaded.scanned == reloaded.total == 1


def _explode(*args, **kwargs):  # pragma: no cover - only reached on a cache miss
    raise AssertionError("cached episodes must not be re-parsed")
