from pathlib import Path

from sts2rl.engine import CombatConfig
from sts2rl.entities import EntityVocab
from sts2rl.ppo import EpisodeRecord, StoredStep
from sts2rl.protocol import parse_state
from sts2rl.sim import SimCombatBackend
from sts2rl.visibility import visibility_audit


VOCAB = Path(__file__).resolve().parents[2] / "schema" / "m2_vocab.json"


def _config(**overrides) -> CombatConfig:
    values = {
        "character": "IRONCLAD",
        "seed": "backend-test",
        "encounter": "SHRINKER_BEETLE_WEAK",
        "hp": 80,
        "max_hp": 80,
        "gold": 99,
        "deck": (
            "BASH+", "STRIKE_IRONCLAD", "STRIKE_IRONCLAD",
            "STRIKE_IRONCLAD", "STRIKE_IRONCLAD",
            "DEFEND_IRONCLAD", "DEFEND_IRONCLAD", "DEFEND_IRONCLAD",
            "DEFEND_IRONCLAD", "DEFEND_IRONCLAD",
        ),
        "relics": ("ANCHOR",),
        "potions": ("BLOCK_POTION", "FIRE_POTION"),
        "draw_order": (
            "BASH+", "DEFEND_IRONCLAD", "DEFEND_IRONCLAD",
            "DEFEND_IRONCLAD", "DEFEND_IRONCLAD",
        ),
    }
    values.update(overrides)
    return CombatConfig(**values)


def test_backend_contract_and_forced_opening_draw() -> None:
    with SimCombatBackend() as backend:
        state = backend.reset_combat(_config())
        assert state.phase == "combat_play"
        assert [
            (card["id"], card["upgraded"])
            for card in state.raw["hand"]
        ] == [
            ("CARD.BASH", True),
            ("CARD.DEFEND_IRONCLAD", False),
            ("CARD.DEFEND_IRONCLAD", False),
            ("CARD.DEFEND_IRONCLAD", False),
            ("CARD.DEFEND_IRONCLAD", False),
        ]
        assert state.raw["player"]["energy"] == state.raw["energy"]
        assert state.raw["player"]["draw_pile_count"] == 5
        assert [row["id"] for row in state.raw["player"]["potions"]] == [
            "POTION.BLOCK_POTION", "POTION.FIRE_POTION",
        ]
        potion = next(candidate for candidate in state.candidates if candidate.action == "use_potion")
        result = backend.step(potion)
        assert result.state.phase == "combat_play"
        assert result.terminated is False


def test_backend_none_fields_keep_each_character_defaults() -> None:
    expected = {
        "IRONCLAD": (80, 10, "RELIC.BURNING_BLOOD"),
        "SILENT": (70, 12, "RELIC.RING_OF_THE_SNAKE"),
        "DEFECT": (75, 10, "RELIC.CRACKED_CORE"),
        "REGENT": (75, 10, "RELIC.DIVINE_RIGHT"),
        "NECROBINDER": (66, 10, "RELIC.BOUND_PHYLACTERY"),
    }
    with SimCombatBackend() as backend:
        for character, (hp, deck_size, relic_id) in expected.items():
            state = backend.reset_combat(CombatConfig(
                character=character,
                seed=f"defaults-{character}",
                encounter="SHRINKER_BEETLE_WEAK",
            ))
            assert state.raw["player"]["hp"] == hp
            assert state.raw["player"]["max_hp"] == hp
            assert state.raw["player"]["deck_size"] == deck_size
            assert [row["id"] for row in state.raw["player"]["relics"]] == [relic_id]


def test_backend_explicit_empty_loadout_overrides_character_defaults() -> None:
    with SimCombatBackend() as backend:
        state = backend.reset_combat(CombatConfig(
            character="SILENT",
            seed="empty-loadout",
            encounter="SHRINKER_BEETLE_WEAK",
            deck=(),
            relics=(),
        ))
        assert state.raw["player"]["deck_size"] == 0
        assert state.raw["player"]["relics"] == []


def test_backend_does_not_offer_passive_potion_as_usable() -> None:
    with SimCombatBackend() as backend:
        state = backend.reset_combat(CombatConfig(
            character="IRONCLAD", seed="passive-potion",
            encounter="SHRINKER_BEETLE_WEAK", hp=80, max_hp=80,
            potions=("FAIRY_INA_BOTTLE",),
        ))
        assert state.raw["player"]["potions"][0]["can_use"] is False
        assert not any(candidate.action == "use_potion" for candidate in state.candidates)


def test_backend_card_and_bundle_select_round_trip() -> None:
    with SimCombatBackend() as backend:
        backend.reset_combat(_config(potions=()))
        selected_cards = []
        backend.combat.request_multi_card_choice(
            prompt="choose cards",
            cards=list(backend.combat.hand[:3]),
            source_pile="hand",
            resolver=lambda cards: selected_cards.extend(cards),
            min_count=1,
            max_count=2,
            allow_skip=True,
        )
        choice = parse_state(backend._raw_state())  # noqa: SLF001 - adapter contract test
        assert choice.phase == "card_select"
        action = next(
            candidate for candidate in choice.candidates
            if candidate.action == "select_cards" and candidate.args["indices"] == "0,1"
        )
        result = backend.step(action)
        assert result.state.phase == "combat_play"
        assert len(selected_cards) == 2

        selected_bundle = []
        backend.request_bundle_choice(
            [backend.combat.hand[:1], backend.combat.hand[1:3]],
            lambda cards: selected_bundle.extend(cards),
        )
        bundle = parse_state(backend._raw_state())  # noqa: SLF001 - adapter contract test
        assert bundle.phase == "bundle_select"
        result = backend.step(bundle.candidates[1])
        assert result.state.phase == "combat_play"
        assert len(selected_bundle) == 2


def test_backend_visibility_audit_has_zero_violations() -> None:
    with SimCombatBackend() as backend:
        state = backend.reset_combat(_config())
        record = EpisodeRecord(
            "backend-visibility",
            [StoredStep(state.raw, state.candidates, 0, 0.0, 0.0, 0.0, None)],
            None,
            False,
        )
        report = visibility_audit([record], EntityVocab.load(VOCAB))
        assert report["violations"] == []


def test_backend_model_catalog_is_stable() -> None:
    with SimCombatBackend() as backend:
        encounters = backend.list_models("encounter")
        assert any(row["id"] == "SHRINKER_BEETLE_WEAK" for row in encounters)
        assert any(row["id"] == "SOUL_FYSH_BOSS" for row in encounters)
        assert {row["category"] for row in encounters} >= {
            "weak", "regular", "elite", "boss",
        }
        assert {row["id"] for row in backend.list_models("card")} >= {
            "BASH", "STRIKE_IRONCLAD",
        }
