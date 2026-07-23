"""Protocol-0.2.0 adapter for the vendored zhiyue combat core."""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence

from sts2rl.engine import CombatConfig
from sts2rl.protocol import ActionCandidate, DecisionState, StepResult, parse_state


REPO_ROOT = Path(__file__).resolve().parents[4]
# The zhiyue combat core is sourced under vendor/ (git-ignored, research-only;
# see scripts/setup_sim.ps1) instead of being committed into this repository.
VENDOR_ROOT = REPO_ROOT / "vendor" / "sts2-rl-agent"


@contextmanager
def _working_directory(path: Path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


class SimCombatBackend:
    """EngineClient-compatible, combat-only in-process backend.

    The backend owns exactly one active combat.  It intentionally exposes no
    map, event, shop, reward, or evaluation-run behavior.
    """

    def __init__(self, vendor_root: Path = VENDOR_ROOT):
        self.vendor_root = Path(vendor_root).resolve()
        if not (self.vendor_root / "sts2_env" / "core" / "combat.py").is_file():
            raise FileNotFoundError(f"vendored sts2_env not found: {self.vendor_root}")
        if str(self.vendor_root) not in sys.path:
            sys.path.insert(0, str(self.vendor_root))
        with _working_directory(self.vendor_root):
            import sts2_env.potions  # noqa: F401
            import sts2_env.powers  # noqa: F401
            from sts2_env.cards.factory import create_card
            from sts2_env.characters.all import create_starting_deck, get_character
            from sts2_env.core.combat import CombatState
            from sts2_env.core.damage import calculate_block, calculate_damage
            from sts2_env.core.enums import CardId, ValueProp
            from sts2_env.core.hooks import modify_damage
            from sts2_env.core.rng import Rng, deterministic_hash_code
            from sts2_env.encounters import act1, act4
            from sts2_env.potions.base import all_potion_models, create_potion
            from sts2_env.run.run_state import RunState

            create_card(CardId.STRIKE_IRONCLAD)
        self._create_card = create_card
        self._create_starting_deck = create_starting_deck
        self._get_character = get_character
        self._create_potion = create_potion
        self._potion_model_ids = {
            self._upper_snake(model.potion_id): model.potion_id
            for model in all_potion_models()
        }
        self._CombatState = CombatState
        self._calculate_block = calculate_block
        self._calculate_damage = calculate_damage
        self._modify_damage = modify_damage
        self._CardId = CardId
        self._ValueProp = ValueProp
        self._Rng = Rng
        self._deterministic_hash_code = deterministic_hash_code
        self._RunState = RunState
        self._encounters: dict[str, Any] = {}
        for module in (act1, act4):
            for name in dir(module):
                setup = getattr(module, name)
                if name.startswith("setup_") and callable(setup):
                    self._encounters[name.removeprefix("setup_").upper()] = setup
        vocab_path = REPO_ROOT / "rl" / "schema" / "m2_vocab.json"
        payload = json.loads(vocab_path.read_text(encoding="utf-8"))
        self._vocab = {kind: set(rows) for kind, rows in payload["entries"].items()}
        self._card_stat_names = {
            value.removeprefix("CARD_STAT.")
            for value in self._vocab.get("card_stat", ())
        }
        self.combat: Any | None = None
        self.config: CombatConfig | None = None
        self._visible_enemy_indices: list[int] = []
        self._pending_bundles: list[list[Any]] | None = None
        self._bundle_resolver: Any | None = None

    def __enter__(self) -> "SimCombatBackend":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        self.combat = None
        self.config = None
        self._visible_enemy_indices = []
        self._pending_bundles = None
        self._bundle_resolver = None

    @staticmethod
    def _character_id(value: str) -> str:
        return "".join(part.title() for part in value.replace("-", "_").split("_"))

    @staticmethod
    def _pascal(value: str) -> str:
        return "".join(part.title() for part in value.split("_"))

    @staticmethod
    def _upper_snake(value: str) -> str:
        return re.sub(r"(?<!^)(?=[A-Z][a-z])", "_", value).upper()

    def _cards(self, ids: Sequence[str]) -> list[Any]:
        cards = []
        for raw_id in ids:
            upgraded = raw_id.endswith("+")
            name = raw_id.removeprefix("CARD.").removesuffix("+")
            # The protocol projection intentionally prefers real-engine ids
            # when they exist (for example FLAME_BARRIER), while the simulator
            # enum calls the same card FLAME_BARRIER_CARD.  Accept either form
            # so snapshots harvested from a zhiyue run round-trip back through
            # reset_combat without a hand-maintained translation table.
            if name not in self._CardId.__members__ and f"{name}_CARD" in self._CardId.__members__:
                name = f"{name}_CARD"
            cards.append(self._create_card(self._CardId[name], upgraded=upgraded))
        return cards

    def _potions(self, ids: Sequence[str]) -> list[Any]:
        result = []
        for slot, raw_id in enumerate(ids):
            name = raw_id.removeprefix("POTION.")
            result.append(self._create_potion(
                # Snapshots use protocol-style upper snake ids.  Single-word
                # potions (FORTIFIER) need the same CamelCase normalization as
                # multi-word ids (BLOCK_POTION -> BlockPotion).  The registry
                # map also preserves embedded capitals such as FairyInABottle.
                self._potion_model_ids.get(name, self._pascal(name)),
                slot=slot,
            ))
        return result

    def reset_combat(self, config: CombatConfig) -> DecisionState:
        setup = self._encounters.get(config.encounter)
        if setup is None:
            raise KeyError(f"sim encounter is not mapped: {config.encounter}")
        character = self._character_id(config.character)
        character_config = self._get_character(character)
        deck = (
            self._create_starting_deck(character)
            if config.deck is None
            else self._cards(config.deck)
        )
        relics = (
            (character_config.starting_relic.name,)
            if config.relics is None
            else config.relics
        )
        potions = self._potions(config.potions or ())
        run = self._RunState(
            seed=config.seed,
            ascension_level=config.ascension,
            character_id=character,
        )
        run.player.deck = deck
        seed = self._deterministic_hash_code(config.seed)
        self.combat = self._CombatState(
            player_hp=(
                config.hp if config.hp is not None
                else character_config.starting_hp
            ),
            player_max_hp=(
                config.max_hp if config.max_hp is not None
                else character_config.starting_hp
            ),
            deck=deck,
            rng_seed=seed,
            relics=relics,
            potions=potions,
            gold=(
                config.gold if config.gold is not None
                else character_config.starting_gold
            ),
            character_id=character,
            player_state=run.player,
            ascension_level=config.ascension,
        )
        if config.draw_order:
            self.combat._forced_draw_order = tuple(config.draw_order)  # noqa: SLF001
        setup(self.combat, self._Rng(seed))
        self.combat.start_combat()
        self.config = config
        self._pending_bundles = None
        self._bundle_resolver = None
        return parse_state(self._raw_state())

    def request_bundle_choice(self, bundles: Sequence[Sequence[Any]], resolver) -> None:
        """Expose a combat-scoped bundle decision for content adapters."""
        if self.combat is None or self.combat.is_over:
            raise RuntimeError("bundle choice requires an active combat")
        self._pending_bundles = [list(bundle) for bundle in bundles]
        self._bundle_resolver = resolver

    def _stable_id(self, kind: str, name: str) -> str:
        prefix = {
            "card": "CARD.", "enemy": "MONSTER.", "power": "POWER.",
            "relic": "RELIC.", "potion": "POTION.", "orb": "ORB.",
        }[kind]
        candidates = [f"{prefix}{name}"]
        if kind == "power":
            candidates.append(f"{prefix}{name}_POWER")
        if kind == "card" and name.endswith("_CARD"):
            candidates.append(f"{prefix}{name[:-5]}")
        for candidate in candidates:
            if candidate in self._vocab[kind]:
                return candidate
        return candidates[0]

    def _powers(self, creature: Any, owner: str) -> list[dict[str, Any]]:
        return [
            {
                "id": self._stable_id("power", power_id.name),
                "amount": int(power.amount),
                "owner": owner,
            }
            for power_id, power in creature.powers.items()
        ]

    def _card_row(self, card: Any, index: int, *, choice: bool = False) -> dict[str, Any]:
        row: dict[str, Any] = {
            "id": self._stable_id("card", card.card_id.name),
            "index": index,
            "cost": int(card.cost),
            "upgraded": bool(card.upgraded),
        }
        if choice:
            return row
        owner = self.combat.primary_player
        row.update({
            "can_play": bool(self.combat.can_play_card(card)),
            "target_type": self._pascal(card.target_type_for(owner).name),
        })
        stats = {}
        for key, value in card.effect_vars.items():
            if not isinstance(value, (int, float)):
                continue
            name = str(key).lower()
            if name not in self._card_stat_names and f"{name}power" in self._card_stat_names:
                name = f"{name}power"
            if name in self._card_stat_names:
                stats[name] = int(value)
        base_damage = int(card.base_damage or 0)
        base_block = int(card.base_block or 0)
        if base_damage > 0:
            target = next(iter(self.combat.hittable_enemies), None)
            stats["damage"] = int(self._calculate_damage(
                base_damage, owner, target, self._ValueProp.MOVE,
                self.combat,
            )) if target is not None else base_damage
        if base_block > 0:
            stats["block"] = int(self._calculate_block(
                base_block, owner, self._ValueProp.MOVE,
                self.combat, card_source=card,
            ))
        row["stats"] = stats or None
        return row

    def _intent_rows(self, enemy: Any, move: Any) -> list[dict[str, Any]]:
        rows = []
        for intent in move.intents:
            damage = int(intent.damage)
            if intent.is_attack:
                damage = int(self._modify_damage(
                    damage, enemy, self.combat.primary_player,
                    self._ValueProp.MOVE, self.combat, card_source=None,
                ))
            intent_type = {
                "MULTI_ATTACK": "Attack",
                "DEATH_BLOW": "Attack",
                "STRONG_DEBUFF": "DebuffStrong",
                "HEAL": "Buff",
                "ESCAPE": "Buff",
            }.get(intent.intent_type.name, self._pascal(intent.intent_type.name))
            rows.append({
                "type": intent_type,
                "damage": damage,
                "hits": int(intent.hits),
                "total_damage": damage * int(intent.hits),
            })
        return rows

    def _player_row(self) -> dict[str, Any]:
        combat = self.combat
        state = combat.current_player_state
        queue = state.orb_queue
        orbs = [] if queue is None else [
            {
                "index": index,
                "slot": index,
                "id": self._stable_id("orb", f"{orb.orb_type.name}_ORB"),
                "passive": int(orb.get_passive_value(combat)),
                "evoke": int(orb.get_evoke_value(combat)),
            }
            for index, orb in enumerate(queue.orbs)
        ]
        return {
            "hp": combat.primary_player.current_hp,
            "max_hp": combat.primary_player.max_hp,
            "block": combat.primary_player.block,
            "energy": combat.energy,
            "max_energy": combat.max_energy,
            "gold": state.player_state.gold,
            "powers": self._powers(combat.primary_player, "player"),
            "deck_size": len(state.starting_deck),
            "deck": [self._card_row(card, index, choice=True) for index, card in enumerate(state.starting_deck)],
            "relics": [
                {"id": self._stable_id("relic", relic.relic_id.name)}
                for relic in state.relics
            ],
            "potions": [
                {
                    "index": index,
                    "id": self._stable_id("potion", self._upper_snake(potion.potion_id)),
                    "target_type": self._pascal(potion.target_type.name),
                    "can_use": (
                        any(combat.can_use_potion(index, target_index=enemy_index)
                            for enemy_index, enemy in enumerate(combat.enemies)
                            if enemy.is_alive and not enemy.escaped)
                        if potion.target_type.name == "ANY_ENEMY"
                        else combat.can_use_potion(index)
                    ),
                }
                for index, potion in enumerate(state.potions)
                if potion is not None
            ],
            "draw_pile_count": len(state.draw),
            "discard_pile_count": len(state.discard),
            "orbs": orbs,
        }

    def _combat_rows(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        visible = [
            (underlying, enemy)
            for underlying, enemy in enumerate(self.combat.enemies)
            if enemy.is_alive and not enemy.escaped
        ]
        self._visible_enemy_indices = [underlying for underlying, _ in visible]
        enemies = []
        for index, (_, enemy) in enumerate(visible):
            move = self.combat.enemy_ais[enemy.combat_id].current_move
            intents = self._intent_rows(enemy, move)
            enemies.append({
                "id": self._stable_id("enemy", enemy.monster_id),
                "index": index,
                "hp": min(int(enemy.current_hp), 1_000),
                "max_hp": min(int(enemy.max_hp), 1_000),
                "block": int(enemy.block),
                "intents": intents,
                "intent": intents[0] if intents else None,
                "intends_attack": any(intent["type"] == "Attack" for intent in intents),
                "powers": self._powers(enemy, f"enemy:{index}"),
            })
        hand = [self._card_row(card, index) for index, card in enumerate(self.combat.hand)]
        return enemies, hand

    def _raw_state(self) -> dict[str, Any]:
        combat = self.combat
        if combat.is_over:
            return {
                "type": "decision", "decision": "game_over",
                "victory": bool(combat.player_won),
                "outcome": "victory" if combat.player_won else "defeat",
                "player": self._player_row(), "enemies": [], "hand": [],
            }
        enemies, hand = self._combat_rows()
        player = self._player_row()
        if self._pending_bundles is not None:
            return {
                "type": "decision", "decision": "bundle_select",
                "bundles": [
                    {
                        "index": bundle_index,
                        "cards": [
                            self._card_row(card, card_index, choice=True)
                            for card_index, card in enumerate(bundle)
                        ],
                    }
                    for bundle_index, bundle in enumerate(self._pending_bundles)
                ],
                "player": player, "enemies": enemies, "hand": hand,
            }
        choice = combat.pending_choice
        if choice is not None:
            return {
                "type": "decision", "decision": "card_select",
                "prompt": choice.prompt,
                "cards": [
                    self._card_row(option.card, index, choice=True)
                    for index, option in enumerate(choice.options)
                ],
                "min_select": choice.min_choices,
                "max_select": choice.max_choices,
                "can_skip": choice.allow_skip,
                "player": player, "enemies": enemies, "hand": hand,
            }
        return {
            "type": "decision", "decision": "combat_play",
            "round": combat.round_number,
            "energy": combat.energy,
            "max_energy": combat.max_energy,
            "draw_pile_count": len(combat.draw_pile),
            "discard_pile_count": len(combat.discard_pile),
            "player_powers": player["powers"],
            "player": player, "enemies": enemies, "hand": hand,
        }

    def step(self, action: ActionCandidate) -> StepResult:
        if self.combat is None:
            raise RuntimeError("reset_combat must be called before step")
        args = action.args
        target = args.get("target_index")
        if target is not None:
            try:
                target = self._visible_enemy_indices[int(target)]
            except (IndexError, ValueError):
                raise RuntimeError(f"invalid visible target index: {target}") from None
        if action.action == "play_card":
            success = self.combat.play_card(int(args["card_index"]), target)
        elif action.action == "use_potion":
            success = self.combat.use_potion(int(args["potion_index"]), target_index=target)
        elif action.action == "end_turn":
            self.combat.end_player_turn()
            success = True
        elif action.action == "skip_select":
            success = self.combat.resolve_pending_choice(None)
        elif action.action == "select_cards":
            indices = [int(value) for value in str(args["indices"]).split(",") if value != ""]
            choice = self.combat.pending_choice
            if choice is None:
                success = False
            elif choice.is_multi:
                success = all(self.combat.resolve_pending_choice(index) for index in indices)
                success = bool(success and self.combat.resolve_pending_choice(None))
            else:
                success = len(indices) == 1 and self.combat.resolve_pending_choice(indices[0])
        elif action.action == "select_bundle":
            index = int(args["bundle_index"])
            if self._pending_bundles is None or not 0 <= index < len(self._pending_bundles):
                success = False
            else:
                selected = self._pending_bundles[index]
                resolver = self._bundle_resolver
                self._pending_bundles = None
                self._bundle_resolver = None
                if resolver is not None:
                    resolver(selected)
                success = True
        else:
            raise NotImplementedError(f"combat action is not supported: {action.action}")
        if not success:
            raise RuntimeError(f"simulator rejected legal action: {action}")
        state = parse_state(self._raw_state())
        return StepResult(state=state, terminated=state.phase == "game_over")

    def list_models(self, kind: str) -> list[dict[str, Any]]:
        if kind == "encounter":
            rows = []
            for encounter_id in sorted(self._encounters):
                category = next(
                    (value for value in ("weak", "normal", "elite", "boss") if encounter_id.lower().endswith(value)),
                    "unknown",
                )
                rows.append({"id": encounter_id, "act": 1, "category": "regular" if category == "normal" else category})
            return rows
        if kind not in self._vocab:
            raise ValueError(f"unsupported model kind: {kind}")
        prefix = {
            "card": "CARD.", "enemy": "MONSTER.", "power": "POWER.",
            "relic": "RELIC.", "potion": "POTION.", "orb": "ORB.",
        }.get(kind, "")
        return [
            {"id": value.removeprefix(prefix)}
            for value in sorted(self._vocab[kind])
            if value != "UNK"
        ]
