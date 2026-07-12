from __future__ import annotations

from sts2rl.engine import CombatConfig


def test_combat_config_minimal_command_omits_player():
    config = CombatConfig("Ironclad", "seed-1", "SLIMES_WEAK")
    assert config.command() == {
        "cmd": "start_combat",
        "character": "Ironclad",
        "seed": "seed-1",
        "ascension": 0,
        "lang": "en",
        "encounter": "SLIMES_WEAK",
    }


def test_combat_config_full_command_carries_overrides():
    config = CombatConfig(
        "Ironclad", "seed-1", "SLIMES_WEAK", ascension=2,
        hp=50, max_hp=70, gold=250,
        deck=("STRIKE_IRONCLAD", "DEFEND_IRONCLAD"),
        relics=("BURNING_BLOOD",), potions=("BLOCK_POTION",),
        draw_order=("STRIKE_IRONCLAD",),
    )
    command = config.command()
    assert command["ascension"] == 2
    assert command["player"] == {
        "hp": 50, "max_hp": 70, "gold": 250,
        "deck": ["STRIKE_IRONCLAD", "DEFEND_IRONCLAD"],
        "relics": ["BURNING_BLOOD"], "potions": ["BLOCK_POTION"],
    }
    assert command["draw_order"] == ["STRIKE_IRONCLAD"]


def test_combat_config_empty_collections_still_override():
    # potions=() must clear the potion slots, not be dropped from the command.
    command = CombatConfig("Ironclad", "s", "SLIMES_WEAK", potions=(), relics=()).command()
    assert command["player"] == {"relics": [], "potions": []}
