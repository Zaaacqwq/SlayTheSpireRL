"""Step-by-step differential harness for the real engine and zhiyue simulator.

The real engine's read-only ``get_draw_pile`` oracle keeps hand/draw/discard
ordering aligned without changing gameplay.  The harness can therefore cover
mixed and upgraded decks, generated cards, relics, and potions while still
reporting the first semantic divergence.
"""

from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any, Iterable, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[3]
RL_SRC = REPO_ROOT / "rl" / "src"
if str(RL_SRC) not in sys.path:
    sys.path.insert(0, str(RL_SRC))

from sts2rl.engine import CombatConfig, EngineClient  # noqa: E402
from sts2rl.protocol import ActionCandidate, DecisionState, parse_state  # noqa: E402


DEFAULT_CLI_ROOT = REPO_ROOT / "external" / "sts2-cli"
DEFAULT_DLL = DEFAULT_CLI_ROOT / "src" / "Sts2Headless" / "bin" / "Debug" / "net9.0" / "Sts2Headless.dll"
DEFAULT_UPSTREAM_ROOT = REPO_ROOT / "vendor" / "sts2-rl-agent"


@dataclass(frozen=True)
class SemanticMismatch:
    field: str
    real: Any
    sim: Any


@dataclass(frozen=True)
class DiffFailure:
    step: int
    action: Mapping[str, Any] | None
    mismatch: SemanticMismatch
    real_state: Mapping[str, Any]
    sim_state: Mapping[str, Any]


@dataclass(frozen=True)
class DiffRun:
    scenario: Mapping[str, Any]
    compared_steps: int
    terminated: bool
    failure: DiffFailure | None

    @property
    def matched(self) -> bool:
        return self.failure is None


def _power_amounts(rows: Iterable[Mapping[str, Any]] | None) -> dict[str, int]:
    return {
        str(row.get("id")): int(row.get("amount", 0))
        for row in rows or ()
        if isinstance(row, Mapping)
    }


def semantic_projection(state: Mapping[str, Any]) -> dict[str, Any]:
    """Fields required by plan section 6.2, normalized for useful diffs."""
    player = state.get("player") or {}
    enemies = []
    for enemy in state.get("enemies") or ():
        intents = enemy.get("intents") or ([] if not enemy.get("intent") else [enemy["intent"]])
        enemies.append({
            "id": enemy.get("id"),
            "index": enemy.get("index"),
            "hp": enemy.get("hp"),
            "block": enemy.get("block"),
            "powers": _power_amounts(enemy.get("powers")),
            "intents": [
                {
                    "type": intent.get("type"),
                    "damage": int(intent.get("damage", 0)),
                    "total_damage": int(intent.get("total_damage", intent.get("damage", 0))),
                }
                for intent in intents
            ],
        })
    return {
        "decision": state.get("decision", state.get("type")),
        "player": {
            "hp": player.get("hp"),
            "block": player.get("block"),
            "energy": state.get("energy", player.get("energy")),
            "powers": _power_amounts(state.get("player_powers") or player.get("powers")),
        },
        "enemies": enemies,
        "hand": dict(sorted(Counter(
            str(card.get("id")) for card in state.get("hand") or ()
        ).items())),
        "draw_pile_count": state.get("draw_pile_count", player.get("draw_pile_count")),
        "discard_pile_count": state.get("discard_pile_count", player.get("discard_pile_count")),
    }


def first_mismatch(real: Mapping[str, Any], sim: Mapping[str, Any]) -> SemanticMismatch | None:
    real_projection = semantic_projection(real)
    sim_projection = semantic_projection(sim)

    def walk(field: str, left: Any, right: Any) -> SemanticMismatch | None:
        if isinstance(left, Mapping) and isinstance(right, Mapping):
            for key in sorted(set(left) | set(right)):
                found = walk(f"{field}.{key}" if field else str(key), left.get(key), right.get(key))
                if found is not None:
                    return found
            return None
        if isinstance(left, list) and isinstance(right, list):
            if len(left) != len(right):
                return SemanticMismatch(f"{field}.length", len(left), len(right))
            for index, (left_item, right_item) in enumerate(zip(left, right)):
                found = walk(f"{field}[{index}]", left_item, right_item)
                if found is not None:
                    return found
            return None
        if left != right:
            return SemanticMismatch(field, left, right)
        return None

    return walk("", real_projection, sim_projection)


@contextmanager
def _working_directory(path: Path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


class ZhiyueCombatDriver:
    """Minimal P1 driver; the production v7 adapter is implemented in P3."""

    def __init__(self, upstream_root: Path):
        self.upstream_root = Path(upstream_root).resolve()
        if not (self.upstream_root / "sts2_env" / "core" / "combat.py").is_file():
            raise FileNotFoundError(f"not a zhiyue checkout: {self.upstream_root}")
        sys.path.insert(0, str(self.upstream_root))
        with _working_directory(self.upstream_root):
            import sts2_env.powers  # noqa: F401
            import sts2_env.potions  # noqa: F401
            from sts2_env.cards.factory import create_card
            from sts2_env.core.combat import CombatState
            from sts2_env.core.enums import CardId, ValueProp
            from sts2_env.core.hooks import modify_damage
            from sts2_env.core.rng import Rng, deterministic_hash_code
            from sts2_env.encounters import act1 as overgrowth_encounters
            from sts2_env.encounters import act4 as underdocks_encounters
            from sts2_env.potions.base import create_potion
            from sts2_env.run.run_state import RunState

            # The factory lazily reads docs/CARDS_REFERENCE.md relative to cwd.
            # Warm it once while cwd is the upstream checkout.
            create_card(CardId.STRIKE_IRONCLAD)
        self._create_card = create_card
        self._create_potion = create_potion
        self._CombatState = CombatState
        self._modify_damage = modify_damage
        self._CardId = CardId
        self._ValueProp = ValueProp
        self._Rng = Rng
        self._deterministic_hash_code = deterministic_hash_code
        self._RunState = RunState
        self._encounters = {}
        for module in (overgrowth_encounters, underdocks_encounters):
            for name in dir(module):
                setup = getattr(module, name)
                if name.startswith("setup_") and callable(setup):
                    self._encounters[name.removeprefix("setup_").upper()] = setup
        vocab_payload = json.loads((REPO_ROOT / "rl" / "schema" / "m2_vocab.json").read_text(encoding="utf-8"))
        self._vocab = {
            kind: set(entries)
            for kind, entries in vocab_payload["entries"].items()
        }
        self.combat = None
        self.config: CombatConfig | None = None
        self._visible_enemy_indices: list[int] = []
        self._hp_locked_enemy_ids: set[int] = set()

    @staticmethod
    def _character_id(value: str) -> str:
        return "".join(part.title() for part in value.replace("-", "_").split("_"))

    def _cards(self, ids: Sequence[str]):
        cards = []
        for raw_id in ids:
            upgraded = raw_id.endswith("+")
            name = raw_id[:-1] if upgraded else raw_id
            name = name.removeprefix("CARD.")
            cards.append(self._create_card(self._CardId[name], upgraded=upgraded))
        return cards

    @staticmethod
    def _upper_snake(value: str) -> str:
        chars: list[str] = []
        for index, char in enumerate(value):
            if char.isupper() and index and (
                value[index - 1].islower()
                or (index + 1 < len(value) and value[index + 1].islower())
            ):
                chars.append("_")
            chars.append(char.upper())
        return "".join(chars)

    def _potions(self, ids: Sequence[str]):
        potions = []
        for slot, raw_id in enumerate(ids):
            name = raw_id.removeprefix("POTION.")
            model_id = self._pascal(name) if "_" in name else name
            potions.append(self._create_potion(model_id, slot=slot))
        return potions

    def reset_combat(self, config: CombatConfig) -> DecisionState:
        setup = self._encounters.get(config.encounter)
        if setup is None:
            raise KeyError(f"P1 encounter is not mapped: {config.encounter}")
        character = self._character_id(config.character)
        deck_ids = config.deck or ("STRIKE_IRONCLAD",) * 10
        deck = self._cards(deck_ids)
        potions = self._potions(config.potions or ())
        run = self._RunState(
            seed=config.seed,
            ascension_level=config.ascension,
            character_id=character,
        )
        run.player.deck = deck
        combat_seed = self._deterministic_hash_code(config.seed)
        self.combat = self._CombatState(
            player_hp=config.hp if config.hp is not None else 80,
            player_max_hp=config.max_hp if config.max_hp is not None else 80,
            deck=deck,
            rng_seed=combat_seed,
            relics=config.relics or (),
            potions=potions,
            gold=config.gold if config.gold is not None else 99,
            character_id=character,
            player_state=run.player,
            ascension_level=config.ascension,
        )
        setup(self.combat, self._Rng(combat_seed))
        self.combat.start_combat()
        if config.draw_order:
            self._set_draw_order(config.draw_order)
        self.config = config
        self._hp_locked_enemy_ids = set()
        return parse_state(self._raw_state())

    def _set_draw_order(self, ids: Sequence[str]) -> None:
        available = list(self.combat.draw_pile)
        ordered = []
        for raw_id in ids:
            wanted = raw_id.removeprefix("CARD.").removesuffix("+")
            match = next((card for card in available if card.card_id.name == wanted), None)
            if match is not None:
                ordered.append(match)
                available.remove(match)
        self.combat.draw_pile[:] = ordered + available

    @staticmethod
    def _card_token(card: Any) -> str:
        card_id = getattr(getattr(card, "card_id", None), "name", "UNKNOWN")
        return f"CARD.{card_id}{'+' if bool(getattr(card, 'upgraded', False)) else ''}"

    @staticmethod
    def _export_token(row: Mapping[str, Any]) -> str:
        card_id = str(row.get("id", "UNKNOWN")).removeprefix("CARD.")
        return f"CARD.{card_id}{'+' if bool(row.get('upgraded', False)) else ''}"

    def sync_piles(
        self,
        oracle: Mapping[str, Any],
    ) -> tuple[DecisionState, SemanticMismatch | None]:
        """Reorder the three visible piles to the oracle without hiding card bugs.

        The combined multiset must already match.  Missing or extra cards are a
        semantic failure; only ordering and pile placement are synchronized.
        """
        pile_names = ("hand", "draw_pile", "discard_pile")
        sim_piles = {
            "hand": self.combat.hand,
            "draw_pile": self.combat.draw_pile,
            "discard_pile": self.combat.discard_pile,
        }
        available = [card for name in pile_names for card in sim_piles[name]]
        real_counter = Counter(
            self._export_token(row)
            for name in pile_names
            for row in oracle.get(name, ())
        )
        sim_counter = Counter(self._card_token(card) for card in available)
        if real_counter != sim_counter:
            mismatch = SemanticMismatch(
                "piles.combined_multiset",
                dict(sorted(real_counter.items())),
                dict(sorted(sim_counter.items())),
            )
            return parse_state(self._raw_state()), mismatch

        by_token: dict[str, list[Any]] = {}
        for card in available:
            by_token.setdefault(self._card_token(card), []).append(card)
        reordered: dict[str, list[Any]] = {}
        for name in pile_names:
            cards = []
            for row in oracle.get(name, ()):
                cards.append(by_token[self._export_token(row)].pop(0))
            reordered[name] = cards
        for name in pile_names:
            sim_piles[name][:] = reordered[name]
        return parse_state(self._raw_state()), None

    def sync_initial_enemy_hp(
        self,
        oracle_state: Mapping[str, Any],
    ) -> tuple[DecisionState, SemanticMismatch | None]:
        """Apply oracle-selected initial HP after requiring identical monsters.

        Real initial HP uses the Niche RNG stream while the vendored encounter
        factory consumes its setup RNG.  P2 records this residual RNG class and
        locks the sampled values so subsequent damage semantics are comparable.
        """
        real_enemies = list(oracle_state.get("enemies") or ())
        sim_enemies = [
            enemy for enemy in self.combat.enemies
            if enemy.is_alive and not enemy.escaped
        ]
        real_ids = [str(enemy.get("id")) for enemy in real_enemies]
        sim_ids = [f"MONSTER.{enemy.monster_id}" for enemy in sim_enemies]
        if real_ids != sim_ids:
            mismatch = SemanticMismatch("enemies.composition", real_ids, sim_ids)
            return parse_state(self._raw_state()), mismatch
        for real_enemy, sim_enemy in zip(real_enemies, sim_enemies, strict=True):
            max_hp = int(real_enemy.get("max_hp", real_enemy.get("hp", sim_enemy.max_hp)))
            hp = int(real_enemy.get("hp", max_hp))
            sim_enemy.max_hp = max_hp
            sim_enemy.current_hp = hp
            self._hp_locked_enemy_ids.add(sim_enemy.combat_id)
        return parse_state(self._raw_state()), None

    def sync_new_enemy_hp(
        self,
        oracle_state: Mapping[str, Any],
    ) -> tuple[DecisionState, SemanticMismatch | None]:
        """Lock Niche-RNG HP only for enemies first seen after combat start."""
        if self.combat.is_over:
            return parse_state(self._raw_state()), None
        sim_enemies = [
            enemy for enemy in self.combat.enemies
            if enemy.is_alive and not enemy.escaped
        ]
        if all(enemy.combat_id in self._hp_locked_enemy_ids for enemy in sim_enemies):
            return parse_state(self._raw_state()), None
        real_enemies = list(oracle_state.get("enemies") or ())
        real_ids = [str(enemy.get("id")) for enemy in real_enemies]
        sim_ids = [f"MONSTER.{enemy.monster_id}" for enemy in sim_enemies]
        if Counter(real_ids) != Counter(sim_ids):
            return parse_state(self._raw_state()), SemanticMismatch(
                "enemies.composition", real_ids, sim_ids,
            )
        remaining = set(range(len(real_enemies)))
        matched: list[tuple[int, Any]] = []
        old_enemies = [
            enemy for enemy in sim_enemies
            if enemy.combat_id in self._hp_locked_enemy_ids
        ]
        new_enemies = [
            enemy for enemy in sim_enemies
            if enemy.combat_id not in self._hp_locked_enemy_ids
        ]
        # Preserve already-compared HP semantics: an existing enemy must find
        # the same observable id/HP row.  This also identifies its row when a
        # summon inserts another monster with the same id ahead of it.
        for sim_enemy in old_enemies:
            sim_id = f"MONSTER.{sim_enemy.monster_id}"
            real_index = next((
                index for index in remaining
                if str(real_enemies[index].get("id")) == sim_id
                and int(real_enemies[index].get("hp", -1)) == sim_enemy.current_hp
            ), None)
            if real_index is None:
                return parse_state(self._raw_state()), SemanticMismatch(
                    "enemies.existing_hp_after_summon",
                    [(row.get("id"), row.get("hp")) for row in real_enemies],
                    [(f"MONSTER.{enemy.monster_id}", enemy.current_hp) for enemy in sim_enemies],
                )
            remaining.remove(real_index)
            matched.append((real_index, sim_enemy))
        for sim_enemy in new_enemies:
            sim_id = f"MONSTER.{sim_enemy.monster_id}"
            real_index = next(
                index for index in remaining
                if str(real_enemies[index].get("id")) == sim_id
            )
            remaining.remove(real_index)
            real_enemy = real_enemies[real_index]
            max_hp = int(real_enemy.get("max_hp", real_enemy.get("hp", sim_enemy.max_hp)))
            sim_enemy.max_hp = max_hp
            sim_enemy.current_hp = int(real_enemy.get("hp", max_hp))
            self._hp_locked_enemy_ids.add(sim_enemy.combat_id)
            matched.append((real_index, sim_enemy))
        ordered = [enemy for _, enemy in sorted(matched)]
        inactive = [enemy for enemy in self.combat.enemies if enemy not in sim_enemies]
        self.combat.enemies[:] = inactive + ordered
        return parse_state(self._raw_state()), None

    def _move_projection(self, enemy: Any, move: Any) -> list[dict[str, Any]]:
        intents = []
        for intent in move.intents:
            damage = int(intent.damage)
            if intent.is_attack:
                damage = int(self._modify_damage(
                    damage,
                    enemy,
                    self.combat.primary_player,
                    self._ValueProp.MOVE,
                    self.combat,
                    card_source=None,
                ))
            intents.append({
                "type": self._intent_type(intent.intent_type.name),
                "damage": damage,
                "total_damage": damage * int(intent.hits),
            })
        return intents

    def sync_enemy_intents(
        self,
        oracle_state: Mapping[str, Any],
    ) -> tuple[DecisionState, SemanticMismatch | None]:
        """Lock only monster-AI branch RNG to an implemented observable move.

        A real intent must match the complete type/damage projection of one of
        the simulator AI's existing move states.  This cannot invent a move or
        conceal an incorrect move implementation; those remain mismatches.
        """
        if self.combat.is_over:
            return parse_state(self._raw_state()), None
        real_enemies = list(oracle_state.get("enemies") or ())
        sim_enemies = [
            enemy for enemy in self.combat.enemies
            if enemy.is_alive and not enemy.escaped
        ]
        real_ids = [str(enemy.get("id")) for enemy in real_enemies]
        sim_ids = [f"MONSTER.{enemy.monster_id}" for enemy in sim_enemies]
        if real_ids != sim_ids:
            return parse_state(self._raw_state()), SemanticMismatch(
                "enemies.composition", real_ids, sim_ids,
            )
        for index, (real_enemy, sim_enemy) in enumerate(
            zip(real_enemies, sim_enemies, strict=True)
        ):
            real_intents = [
                {
                    "type": intent.get("type"),
                    "damage": int(intent.get("damage", 0)),
                    "total_damage": int(intent.get("total_damage", intent.get("damage", 0))),
                }
                for intent in real_enemy.get("intents") or ()
            ]
            ai = self.combat.enemy_ais[sim_enemy.combat_id]
            if self._move_projection(sim_enemy, ai.current_move) == real_intents:
                continue
            matches = [
                state for state in ai.states.values()
                if state.is_move and self._move_projection(sim_enemy, state) == real_intents
            ]
            if not matches:
                return parse_state(self._raw_state()), SemanticMismatch(
                    f"enemies[{index}].intents.oracle_move",
                    real_intents,
                    self._move_projection(sim_enemy, ai.current_move),
                )
            ai._current_state_id = matches[0].state_id  # noqa: SLF001 - test RNG lock
        return parse_state(self._raw_state()), None

    @staticmethod
    def _pascal(enum_name: str) -> str:
        return "".join(part.title() for part in enum_name.split("_"))

    @classmethod
    def _intent_type(cls, enum_name: str) -> str:
        # Protocol 0.2.0 represents both single and repeated attacks as
        # ``Attack``; repetition is carried by total_damage.
        if enum_name == "MULTI_ATTACK":
            return "Attack"
        return cls._pascal(enum_name)

    def _stable_id(self, kind: str, name: str) -> str:
        prefix = {"card": "CARD.", "power": "POWER."}[kind]
        candidates = [f"{prefix}{name}"]
        if kind == "power":
            candidates.append(f"{prefix}{name}_POWER")
        if kind == "card" and name.endswith("_CARD"):
            candidates.append(f"{prefix}{name[:-5]}")
        for candidate in candidates:
            if candidate in self._vocab[kind]:
                return candidate
        # Keeping the unknown visible is safer than silently mapping it to a
        # different known entity.  P3's visibility audit will reject this.
        return candidates[0]

    def _powers(self, creature: Any, owner: str) -> list[dict[str, Any]] | None:
        rows = [
            {"id": self._stable_id("power", power_id.name), "amount": int(power.amount), "owner": owner}
            for power_id, power in creature.powers.items()
        ]
        return rows or None

    def _raw_state(self) -> dict[str, Any]:
        combat = self.combat
        if combat.is_over:
            return {
                "type": "decision",
                "decision": "game_over",
                "victory": bool(combat.player_won),
                "outcome": "victory" if combat.player_won else "defeat",
                "player": {
                    "hp": combat.primary_player.current_hp,
                    "max_hp": combat.primary_player.max_hp,
                    "block": combat.primary_player.block,
                },
                "enemies": [],
                "hand": [],
                "energy": combat.energy,
                "draw_pile_count": len(combat.draw_pile),
                "discard_pile_count": len(combat.discard_pile),
            }
        enemies = []
        visible_enemies = [
            (underlying_index, enemy)
            for underlying_index, enemy in enumerate(combat.enemies)
            if enemy.is_alive and not enemy.escaped
        ]
        self._visible_enemy_indices = [underlying_index for underlying_index, _ in visible_enemies]
        for index, (_, enemy) in enumerate(visible_enemies):
            move = combat.enemy_ais[enemy.combat_id].current_move
            intents = self._move_projection(enemy, move)
            enemies.append({
                "id": f"MONSTER.{enemy.monster_id}",
                "index": index,
                "hp": enemy.current_hp,
                "max_hp": enemy.max_hp,
                "block": enemy.block,
                "intents": intents,
                "intends_attack": any(intent.is_attack for intent in move.intents),
                "powers": self._powers(enemy, f"enemy:{index}"),
            })
        hand = []
        for index, card in enumerate(combat.hand):
            hand.append({
                "id": f"CARD.{card.card_id.name}",
                "index": index,
                "cost": int(card.cost),
                "upgraded": bool(card.upgraded),
                "can_play": bool(combat.can_play_card(card)),
                "target_type": self._pascal(card.target_type_for(combat.primary_player).name),
            })
        player_state = combat.current_player_state
        return {
            "type": "decision",
            "decision": "combat_play",
            "round": combat.round_number,
            "energy": combat.energy,
            "max_energy": combat.max_energy,
            "draw_pile_count": len(combat.draw_pile),
            "discard_pile_count": len(combat.discard_pile),
            "player": {
                "hp": combat.primary_player.current_hp,
                "max_hp": combat.primary_player.max_hp,
                "block": combat.primary_player.block,
                "gold": player_state.player_state.gold,
                "deck_size": len(player_state.starting_deck),
                "deck": [{"id": f"CARD.{card.card_id.name}", "upgraded": card.upgraded} for card in player_state.starting_deck],
                "relics": [{"id": f"RELIC.{relic.relic_id.name}"} for relic in combat.relics],
                "potions": [
                    {
                        "index": index,
                        "id": f"POTION.{self._upper_snake(potion.potion_id)}",
                        "target_type": self._pascal(potion.target_type.name),
                        "can_use": bool(potion.can_use_in_combat()),
                    }
                    for index, potion in enumerate(combat.potions)
                    if potion is not None
                ],
            },
            "player_powers": self._powers(combat.primary_player, "player"),
            "hand": hand,
            "enemies": enemies,
        }

    def step(self, action: ActionCandidate) -> DecisionState:
        args = action.args
        target_index = args.get("target_index")
        if target_index is not None:
            try:
                target_index = self._visible_enemy_indices[int(target_index)]
            except (IndexError, ValueError):
                raise RuntimeError(f"invalid visible target index: {target_index}") from None
        if action.action == "play_card":
            success = self.combat.play_card(args["card_index"], target_index)
        elif action.action == "end_turn":
            self.combat.end_player_turn()
            success = True
        elif action.action == "use_potion":
            success = self.combat.use_potion(args["potion_index"], target_index=target_index)
        else:
            raise NotImplementedError(f"P1 action is not implemented: {action.action}")
        if not success:
            raise RuntimeError(f"simulator rejected legal action: {action}")
        return parse_state(self._raw_state())


def _select_shared_action(real: DecisionState, sim: DecisionState) -> ActionCandidate:
    # ``ActionCandidate.args`` is a mapping and therefore intentionally not
    # hashable.  Candidate lists are tiny, so equality matching is clearer than
    # inventing a second canonical representation here.
    shared = [candidate for candidate in real.candidates if candidate in sim.candidates]
    if not shared:
        raise RuntimeError(
            "no shared action candidate: "
            f"real={[c.command() for c in real.candidates]!r} "
            f"sim={[c.command() for c in sim.candidates]!r}"
        )
    # Consume configured potions so the highest P2 loadout tier actually covers
    # their effects, then exercise cards before ending the turn.
    return next(
        (candidate for action in ("use_potion", "play_card")
         for candidate in shared if candidate.action == action),
        shared[0],
    )


def sync_oracle_piles(
    real: EngineClient,
    sim: ZhiyueCombatDriver,
    real_state: DecisionState,
    sim_state: DecisionState,
) -> tuple[DecisionState, SemanticMismatch | None]:
    if real_state.phase not in {"combat_play", "card_select", "bundle_select"}:
        return sim_state, None
    command = {"cmd": "get_draw_pile"}
    real.trace.append(command)
    response = real._request(command)  # noqa: SLF001 - test-only oracle extension
    if response.get("type") != "ok":
        raise RuntimeError(f"get_draw_pile failed: {response!r}")
    return sim.sync_piles(response)


def _action_record(action: ActionCandidate, state: DecisionState) -> dict[str, Any]:
    record = action.command()
    args = action.args
    if action.action == "play_card":
        card_index = args.get("card_index")
        card = next(
            (row for row in state.raw.get("hand") or () if row.get("index") == card_index),
            None,
        )
        if card is not None:
            record["observed_card"] = {
                "id": card.get("id"),
                "upgraded": bool(card.get("upgraded", False)),
            }
    elif action.action == "use_potion":
        potion_index = args.get("potion_index")
        potion = next(
            (row for row in (state.raw.get("player") or {}).get("potions") or ()
             if row.get("index") == potion_index),
            None,
        )
        if potion is not None:
            record["observed_potion"] = {"id": potion.get("id")}
    return record


def _terminal_player_mismatch(
    real_state: Mapping[str, Any],
    sim_state: Mapping[str, Any],
    *,
    normalize_card_reward: bool = False,
) -> SemanticMismatch | None:
    real_player = real_state.get("player") or {}
    sim_player = sim_state.get("player") or {}
    real_hp = real_player.get("hp")
    sim_hp = sim_player.get("hp")
    real_max_hp = real_player.get("max_hp")
    sim_max_hp = sim_player.get("max_hp")
    if normalize_card_reward:
        # Some elite rewards are applied before the engine exposes card_reward
        # (for example Strawberry adds 7 max/current HP).  That mutation is
        # outside the atomic combat boundary; remove only an equal max/current
        # HP increase before comparing the final combat HP.
        max_gain = max(0, int(real_max_hp or 0) - int(sim_max_hp or 0))
        if int(real_hp or 0) - max_gain != int(sim_hp or 0):
            return SemanticMismatch(
                "terminal.player.hp",
                real_hp,
                sim_hp,
            )
    else:
        for field in ("hp", "max_hp"):
            if real_player.get(field) != sim_player.get(field):
                return SemanticMismatch(
                    f"terminal.player.{field}",
                    real_player.get(field),
                    sim_player.get(field),
                )
    if real_player.get("block") != sim_player.get("block"):
        return SemanticMismatch(
            "terminal.player.block",
            real_player.get("block"),
            sim_player.get("block"),
        )
    return None


def run_diff(
    real: EngineClient,
    sim: ZhiyueCombatDriver,
    config: CombatConfig,
    *,
    max_steps: int = 20,
    sync_initial_enemy_hp: bool = False,
    sync_enemy_intents: bool = False,
) -> DiffRun:
    real_state = real.reset_combat(config)
    sim_state = sim.reset_combat(config)
    scenario = config.command()
    sim_state, pile_mismatch = sync_oracle_piles(real, sim, real_state, sim_state)
    if pile_mismatch is not None:
        return DiffRun(scenario, 0, False, DiffFailure(
            0, None, pile_mismatch, real_state.raw, sim_state.raw,
        ))
    if sync_initial_enemy_hp:
        sim_state, hp_mismatch = sim.sync_initial_enemy_hp(real_state.raw)
        if hp_mismatch is not None:
            return DiffRun(scenario, 0, False, DiffFailure(
                0, None, hp_mismatch, real_state.raw, sim_state.raw,
            ))
    if sync_enemy_intents:
        sim_state, intent_mismatch = sim.sync_enemy_intents(real_state.raw)
        if intent_mismatch is not None:
            return DiffRun(scenario, 0, False, DiffFailure(
                0, None, intent_mismatch, real_state.raw, sim_state.raw,
            ))
    mismatch = first_mismatch(real_state.raw, sim_state.raw)
    if mismatch is not None:
        return DiffRun(scenario, 0, False, DiffFailure(
            0, None, mismatch, real_state.raw, sim_state.raw,
        ))

    for step in range(1, max_steps + 1):
        if real_state.phase == "game_over" or sim_state.phase == "game_over":
            return DiffRun(scenario, step - 1, True, None)
        action = _select_shared_action(real_state, sim_state)
        action_record = _action_record(action, real_state)
        real_state = real.step(action).state
        sim_state = sim.step(action)
        sim_state, pile_mismatch = sync_oracle_piles(real, sim, real_state, sim_state)
        if pile_mismatch is not None:
            return DiffRun(scenario, step, False, DiffFailure(
                step, action_record, pile_mismatch, real_state.raw, sim_state.raw,
            ))
        if sync_initial_enemy_hp:
            sim_state, hp_mismatch = sim.sync_new_enemy_hp(real_state.raw)
            if hp_mismatch is not None:
                return DiffRun(scenario, step, False, DiffFailure(
                    step, action_record, hp_mismatch, real_state.raw, sim_state.raw,
                ))
        if sync_enemy_intents:
            sim_state, intent_mismatch = sim.sync_enemy_intents(real_state.raw)
            if intent_mismatch is not None:
                return DiffRun(scenario, step, False, DiffFailure(
                    step, action_record, intent_mismatch, real_state.raw, sim_state.raw,
                ))
        if real_state.phase == "game_over" and sim_state.phase == "game_over":
            mismatch = _terminal_player_mismatch(real_state.raw, sim_state.raw)
            if mismatch is not None:
                return DiffRun(scenario, step, False, DiffFailure(
                    step, action_record, mismatch, real_state.raw, sim_state.raw,
                ))
            real_outcome = real_state.raw.get("outcome")
            sim_outcome = sim_state.raw.get("outcome")
            if real_outcome is not None and sim_outcome is not None and real_outcome != sim_outcome:
                mismatch = SemanticMismatch("terminal.outcome", real_outcome, sim_outcome)
                return DiffRun(scenario, step, False, DiffFailure(
                    step, action_record, mismatch, real_state.raw, sim_state.raw,
                ))
            return DiffRun(scenario, step, True, None)
        # The real atomic-combat oracle still lives inside a full run, so a
        # victory advances to ``card_reward``.  The combat-only backend must
        # instead terminate at ``game_over``.  Treat those boundaries as the
        # same only after checking the post-combat player state.
        if sim_state.phase == "game_over" and real_state.phase == "card_reward":
            mismatch = _terminal_player_mismatch(
                real_state.raw,
                sim_state.raw,
                normalize_card_reward=True,
            )
            if mismatch is not None:
                return DiffRun(scenario, step, False, DiffFailure(
                    step, action_record, mismatch, real_state.raw, sim_state.raw,
                ))
            if not sim_state.raw.get("victory", False):
                mismatch = SemanticMismatch("terminal.victory", True, False)
                return DiffRun(scenario, step, False, DiffFailure(
                    step, action_record, mismatch, real_state.raw, sim_state.raw,
                ))
            return DiffRun(scenario, step, True, None)
        mismatch = first_mismatch(real_state.raw, sim_state.raw)
        if mismatch is not None:
            return DiffRun(scenario, step, False, DiffFailure(
                step, action_record, mismatch, real_state.raw, sim_state.raw,
            ))
    return DiffRun(scenario, max_steps, False, None)


def _jsonable(run: DiffRun) -> dict[str, Any]:
    payload = asdict(run)
    payload["matched"] = run.matched
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--upstream", type=Path, default=DEFAULT_UPSTREAM_ROOT,
        help="directory containing the vendored/top-level sts2_env package",
    )
    parser.add_argument("--cli-root", type=Path, default=DEFAULT_CLI_ROOT)
    parser.add_argument("--dll", type=Path, default=DEFAULT_DLL)
    parser.add_argument("--seed", default="sim-p1-0")
    parser.add_argument("--encounter", default="SHRINKER_BEETLE_WEAK")
    parser.add_argument("--max-steps", type=int, default=20)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    dotnet = shutil.which("dotnet") or "dotnet"
    config = CombatConfig(
        character="IRONCLAD",
        seed=args.seed,
        encounter=args.encounter,
        hp=80,
        max_hp=80,
        gold=99,
        deck=("STRIKE_IRONCLAD",) * 10,
        relics=("BURNING_BLOOD",),
        potions=(),
        draw_order=("STRIKE_IRONCLAD",) * 5,
    )
    sim = ZhiyueCombatDriver(args.upstream)
    with EngineClient([dotnet, str(args.dll.resolve())], cwd=args.cli_root.resolve(), timeout=30) as real:
        result = run_diff(real, sim, config, max_steps=args.max_steps)
    payload = _jsonable(result)
    rendered = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if result.matched else 1


if __name__ == "__main__":
    raise SystemExit(main())
