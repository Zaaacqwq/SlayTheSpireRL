from __future__ import annotations

from pathlib import Path

import pytest

from sts2rl.engine import CombatConfig

from .diff_harness import DEFAULT_UPSTREAM_ROOT, ZhiyueCombatDriver, semantic_projection


@pytest.fixture(scope="module")
def simulator() -> ZhiyueCombatDriver:
    return ZhiyueCombatDriver(Path(DEFAULT_UPSTREAM_ROOT))


def _config(encounter: str, *, ascension: int = 0) -> CombatConfig:
    return CombatConfig(
        character="IRONCLAD",
        seed="probe-a",
        encounter=encounter,
        ascension=ascension,
        hp=80,
        max_hp=80,
        gold=99,
        deck=("STRIKE_IRONCLAD",) * 10,
        relics=(),
        potions=(),
    )


@pytest.mark.parametrize(
    ("encounter", "ascension", "hp", "damage", "powers"),
    [
        ("BYRDONIS_ELITE", 0, 84, 17, {"POWER.TERRITORIAL_POWER": 1}),
        ("BYRDONIS_ELITE", 9, 90, 19, {"POWER.TERRITORIAL_POWER": 1}),
        ("TERROR_EEL_ELITE", 0, 140, 16, {"POWER.SHRIEK_POWER": 70}),
        ("TERROR_EEL_ELITE", 9, 150, 18, {"POWER.SHRIEK_POWER": 75}),
        ("VANTOM_BOSS", 0, 173, 7, {"POWER.SLIPPERY_POWER": 8}),
        ("VANTOM_BOSS", 9, 183, 8, {"POWER.SLIPPERY_POWER": 9}),
    ],
)
def test_current_game_elite_and_boss_values(
    simulator: ZhiyueCombatDriver,
    encounter: str,
    ascension: int,
    hp: int,
    damage: int,
    powers: dict[str, int],
) -> None:
    state = simulator.reset_combat(_config(encounter, ascension=ascension))
    enemy = semantic_projection(state.raw)["enemies"][0]
    assert enemy["hp"] == hp
    assert enemy["intents"][0]["damage"] == damage
    assert enemy["powers"] == powers


@pytest.mark.parametrize(("ascension", "hp"), [(0, 240), (9, 250)])
def test_waterfall_giant_current_hp(
    simulator: ZhiyueCombatDriver,
    ascension: int,
    hp: int,
) -> None:
    state = simulator.reset_combat(_config("WATERFALL_GIANT_BOSS", ascension=ascension))
    assert semantic_projection(state.raw)["enemies"][0]["hp"] == hp


def test_current_game_underdocks_compositions(simulator: ZhiyueCombatDriver) -> None:
    merc = simulator.reset_combat(_config("GREMLIN_MERC_NORMAL"))
    assert [enemy["id"] for enemy in semantic_projection(merc.raw)["enemies"]] == [
        "MONSTER.GREMLIN_MERC"
    ]

    seapunk = simulator.reset_combat(_config("SEAPUNK_NORMAL"))
    assert [enemy["id"] for enemy in semantic_projection(seapunk.raw)["enemies"]] == [
        "MONSTER.CALCIFIED_CULTIST",
        "MONSTER.SEAPUNK",
    ]


def test_nibbit_slice_exposes_attack_and_defend(simulator: ZhiyueCombatDriver) -> None:
    state = simulator.reset_combat(_config("NIBBITS_NORMAL", ascension=9))
    front = semantic_projection(state.raw)["enemies"][0]
    assert front["intents"] == [
        {"type": "Attack", "damage": 7, "total_damage": 7},
        {"type": "Defend", "damage": 0, "total_damage": 0},
    ]


def test_haunted_ship_opens_with_weak_and_dazed(simulator: ZhiyueCombatDriver) -> None:
    state = simulator.reset_combat(_config("HAUNTED_SHIP_NORMAL"))
    enemy = semantic_projection(state.raw)["enemies"][0]
    assert [intent["type"] for intent in enemy["intents"]] == ["Debuff", "StatusCard"]

    end_turn = next(action for action in state.candidates if action.action == "end_turn")
    after = simulator.step(end_turn)
    projection = semantic_projection(after.raw)
    assert projection["player"]["powers"] == {"POWER.WEAK_POWER": 3}
    assert projection["discard_pile_count"] == 10


def test_cubex_construct_has_artifact_but_no_opening_block(
    simulator: ZhiyueCombatDriver,
) -> None:
    state = simulator.reset_combat(_config("CUBEX_CONSTRUCT_NORMAL"))
    enemy = semantic_projection(state.raw)["enemies"][0]
    assert enemy["block"] == 0
    assert enemy["powers"] == {"POWER.ARTIFACT_POWER": 1}
