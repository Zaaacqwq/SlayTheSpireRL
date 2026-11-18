from __future__ import annotations

import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "m2_train", REPO_ROOT / "tools" / "m2_train.py"
)
trainer = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(trainer)


def test_passing_gate_is_persisted_as_the_resumed_stage():
    assert trainer.stage_index_after_evaluation(1, 2, "mixed_combat", 0.80) == 2


def test_failing_or_capped_gate_keeps_the_current_stage():
    assert trainer.stage_index_after_evaluation(1, 2, "mixed_combat", 0.59) == 1
    assert trainer.stage_index_after_evaluation(2, 2, "act1", 1.0) == 2


class _Step:
    def __init__(self, raw_state):
        self.raw_state = raw_state


class _Record:
    def __init__(self, steps):
        self.steps = [_Step(state) for state in steps]


def boss_state(name: str, act: int, hp: int = 80, deck=("STRIKE_IRONCLAD",)):
    return {
        "decision": "combat_play", "round": 1,
        "context": {"act": act, "floor": act * 15.0, "room_type": "Boss",
                    "boss": {"id": name, "name": name}},
        "player": {"hp": hp, "max_hp": 80, "relics": [], "potions": [],
                   "deck": [{"id": f"CARD.{card}"} for card in deck]},
    }


def other_state(room: str = "Map"):
    return {"decision": "map_select", "round": 0,
            "context": {"act": 1, "floor": 1.0, "room_type": room}, "player": {"hp": 80}}


def test_a_run_contributes_every_boss_it_meets_not_just_the_first():
    """The act-2 boss can only enter the buffer if collection does not stop at act 1.

    Stopping at the first boss produced a 256/256 act-1 buffer on
    m2_v7_acts12_hpshape, so 15% of each iteration retrained solved fights.
    """
    buffer: list = []
    record = _Record([
        boss_state("A1_BOSS", 1), boss_state("A1_BOSS", 1),  # same fight, round 1
        other_state(), boss_state("A2_BOSS", 2),
    ])

    added = trainer.update_on_policy_boss_buffer(buffer, [record], max_size=64)

    assert added == 2
    assert [row.encounter for row in buffer] == ["A1_BOSS", "A2_BOSS"]


def test_retention_gives_every_boss_a_share_instead_of_the_most_common_one():
    # act 1 is met by every run, act 2 only by the runs that get there
    buffer = [
        *(trainer.Loadout(80, 80, ("S",), (), (), "A1_BOSS") for _ in range(50)),
        *(trainer.Loadout(70, 80, ("S",), (), (), "A2_BOSS") for _ in range(4)),
    ]

    trainer.trim_boss_buffer(buffer, max_size=12)

    kept = [row.encounter for row in buffer]
    assert len(kept) == 12
    # 12 // 2 encounters = 6 each; act 2 only has 4, so its slack goes to the
    # newest loadouts overall rather than leaving the buffer under capacity
    assert kept.count("A2_BOSS") == 4
    assert kept.count("A1_BOSS") == 8


def test_replay_pool_covers_the_buffer_so_act2_snapshots_are_not_redirected():
    """An act-2 loadout in an act-1-only pool silently fights an act-1 boss.

    ``episode_config`` re-samples the encounter whenever the loadout's own is
    absent from the stage, so the balanced buffer from ``trim_boss_buffer`` was
    still spending half its replays on act 1 (boss_replay_win_rate 0.89-0.94,
    i.e. act 1's dev rate, while act 2 sat at 0.50-0.64).
    """
    buffer = [
        trainer.Loadout(80, 80, ("S",), (), (), "A1_BOSS"),
        trainer.Loadout(55, 80, ("S",), (), (), "A2_BOSS"),
    ]

    pool = trainer.replay_encounters(("A1_BOSS",), buffer)

    assert pool == ("A1_BOSS", "A2_BOSS")
    # every buffered snapshot must be replayable as the fight it was taken from
    assert all(row.encounter in pool for row in buffer)


def test_replay_pool_keeps_the_bootstrap_encounters_while_the_buffer_is_empty():
    assert trainer.replay_encounters(("A1_BOSS",), []) == ("A1_BOSS",)


def test_retention_is_a_no_op_below_capacity():
    buffer = [trainer.Loadout(80, 80, ("S",), (), (), "A1_BOSS") for _ in range(3)]

    trainer.trim_boss_buffer(buffer, max_size=64)

    assert len(buffer) == 3
