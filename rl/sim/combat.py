"""Single-combat state machine for the Phase B v1 simulator.

Implements Slay the Spire 2's combat rules for the curated content set in
rl/data/*.json. Mechanics: energy/hand/draw/discard/exhaust, block, HP,
Strength, Dexterity, Vulnerable, Weak, Frail (v1's original scope), plus,
as of the 2026-07-10 Stage 2 entry in plan/plan.md, a data-driven
triggered-Power system (turn-start / on-exhaust / on-HP-lost-this-turn /
on-block-gained / on-Vulnerable-applied hooks) and a generic `conditional`
effect wrapper for "if you did X this turn" cards. Still no relics,
potions, map/run structure, recursive auto-play, dynamic card costs, or
card-spawning effects -- see plan/rl_roadmap.md for what's deferred.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from . import powers
from .card import CardInstance, load_card_defs

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
MONSTERS_DIR = DATA_DIR / "monsters"
# v1's default combined pool is both Act 1 regions (the only ones with any
# real content so far). act2_hive.json/act3_glory.json are Stage 4
# placeholders per plan/plan.md's roadmap -- callers who want a specific
# region pass their own `paths` list.
DEFAULT_MONSTER_PATHS: tuple[Path, ...] = (
    MONSTERS_DIR / "act1_overgrowth.json",
    MONSTERS_DIR / "act1_underdocks.json",
)

MAX_HAND_SIZE = 10
STARTING_HAND_SIZE = 5
DEFAULT_MAX_ENERGY = 3
DEFAULT_PLAYER_MAX_HP = 80


def load_monster_data(paths: Iterable[Path] = DEFAULT_MONSTER_PATHS) -> dict:
    """Loads and merges one or more monsters/<region>.json files."""
    monsters: dict = {}
    encounters: dict = {}
    for path in paths:
        raw = json.loads(path.read_text(encoding="utf-8"))
        monsters.update(raw.get("monsters", {}))
        encounters.update(raw.get("encounters", {}))
    return {"monsters": monsters, "encounters": encounters}


@dataclass
class EnemyState:
    monster_id: str
    display_name: str
    hp: int
    max_hp: int
    ai_cycle: tuple[dict, ...]
    block: int = 0
    strength: int = 0
    vulnerable: int = 0
    weak: int = 0
    turn_index: int = 0
    alive: bool = True
    # Set by the player's Mangle-style "enemy loses N Strength this turn"
    # effects (negative value). Applies to this enemy's very next turn, then
    # reset to 0 right after that turn resolves -- see _run_enemy_turn().
    temp_strength_penalty: int = 0

    def current_intent(self) -> dict:
        if not self.ai_cycle:
            return {}
        return self.ai_cycle[self.turn_index % len(self.ai_cycle)]

    @property
    def effective_strength(self) -> int:
        return self.strength + self.temp_strength_penalty


@dataclass
class PlayerState:
    hp: int
    max_hp: int
    block: int = 0
    strength: int = 0
    temp_strength: int = 0
    dexterity: int = 0
    vulnerable: int = 0
    weak: int = 0
    frail: int = 0
    energy: int = DEFAULT_MAX_ENERGY
    max_energy: int = DEFAULT_MAX_ENERGY
    hand: list[CardInstance] = field(default_factory=list)
    draw_pile: list[CardInstance] = field(default_factory=list)
    discard_pile: list[CardInstance] = field(default_factory=list)
    exhaust_pile: list[CardInstance] = field(default_factory=list)
    # Stage 2 triggered-Power system. Each value is a "triggers" dict whose
    # keys are either an event name (-> effects list, see
    # _fire_power_effects) or the special key "magnitude" (-> a plain int)
    # for powers that are continuously-checked numeric parameters rather
    # than event-fired effects (currently just Cruelty's damage-bonus
    # percent). Captured at gain_power time from the granting card's own
    # base/upgraded data, so upgraded magnitudes (e.g. Demon Form+ granting
    # 3 Strength/turn instead of 2) are correct without the trigger tables
    # needing to know about upgrades themselves. Presence of a power_id key
    # means "active"; `powers` persists for the whole combat, `temp_powers`
    # is cleared every _start_player_turn() (reserved for Stage 2b's "this
    # turn only" powers like Rage -- unused by any Stage 2 card, but the
    # lifecycle exists).
    powers: dict[str, dict[str, tuple[dict, ...]]] = field(default_factory=dict)
    temp_powers: dict[str, dict[str, tuple[dict, ...]]] = field(default_factory=dict)
    # Turn-scoped flags, all reset in _start_player_turn().
    exhausted_a_card_this_turn: bool = False
    lost_hp_this_turn: bool = False
    first_block_gain_used_this_turn: bool = False  # Unmovable
    no_draw_this_turn: bool = False  # Battle Trance

    @property
    def effective_strength(self) -> int:
        return self.strength + self.temp_strength


class Combat:
    """One single-combat encounter. Mutates in place; no undo."""

    def __init__(
        self,
        deck: list[CardInstance],
        enemy_ids: list[str],
        card_defs: dict | None = None,
        monster_data: dict | None = None,
        rng: random.Random | None = None,
        player_max_hp: int = DEFAULT_PLAYER_MAX_HP,
    ):
        self.defs = card_defs or load_card_defs()
        self.monster_data = monster_data or load_monster_data()
        self.rng = rng or random.Random()

        self.player = PlayerState(hp=player_max_hp, max_hp=player_max_hp)
        self.player.draw_pile = list(deck)
        self.rng.shuffle(self.player.draw_pile)

        self.enemies: list[EnemyState] = [self._make_enemy(mid) for mid in enemy_ids]
        self.turn = "player"
        self.round_num = 1
        self.outcome: str | None = None  # "win" | "loss" | None
        self.log: list[str] = []

        self._draw_cards(STARTING_HAND_SIZE)

    # ------------------------------------------------------------------
    # setup helpers
    # ------------------------------------------------------------------
    def _make_enemy(self, monster_id: str) -> EnemyState:
        m = self.monster_data["monsters"][monster_id]
        return EnemyState(
            monster_id=monster_id,
            display_name=m["display_name"],
            hp=m["hp"],
            max_hp=m["hp"],
            ai_cycle=tuple(m["ai_cycle"]),
        )

    def alive_enemies(self) -> list[EnemyState]:
        return [e for e in self.enemies if e.alive]

    def whole_deck(self) -> list[CardInstance]:
        return (
            self.player.hand
            + self.player.draw_pile
            + self.player.discard_pile
            + self.player.exhaust_pile
        )

    def _draw_cards(self, n: int) -> None:
        for _ in range(n):
            if len(self.player.hand) >= MAX_HAND_SIZE:
                break
            if not self.player.draw_pile:
                if not self.player.discard_pile:
                    break
                self.player.draw_pile, self.player.discard_pile = self.player.discard_pile, []
                self.rng.shuffle(self.player.draw_pile)
            self.player.hand.append(self.player.draw_pile.pop())

    def _damage_enemy(self, enemy: EnemyState, dmg: int) -> None:
        new_hp, new_block = powers.apply_damage_to_combatant(enemy.hp, enemy.block, dmg)
        enemy.hp, enemy.block = new_hp, new_block
        if enemy.hp <= 0:
            enemy.alive = False

    def _damage_player(self, dmg: int) -> None:
        if "TANK" in self.player.powers:
            dmg *= 2
        new_hp, new_block = powers.apply_damage_to_combatant(
            self.player.hp, self.player.block, dmg
        )
        self.player.hp, self.player.block = new_hp, new_block

    def _player_attack_damage(self, base_amount: int, target: EnemyState) -> int:
        """Centralizes all player-sourced attack damage math so passive
        damage modifiers (currently just Cruelty) only need one call site
        instead of being threaded through every damage-dealing effect type."""
        cruelty = self.player.powers.get("CRUELTY")
        cruelty_percent = cruelty.get("magnitude", 0) if cruelty else 0
        return powers.compute_attack_damage(
            base_amount,
            self.player.effective_strength,
            self.player.weak,
            target.vulnerable,
            cruelty_percent=cruelty_percent,
        )

    # ------------------------------------------------------------------
    # Stage 2: triggered-Power system
    # ------------------------------------------------------------------
    def _fire_power_effects(self, event: str) -> None:
        """Fires every active power's effects registered for `event`
        (one of "turn_start" / "on_exhaust" / "hp_lost_own_turn" /
        "block_gained" / "vulnerable_applied"). Effects are looked up from
        the triggers dict captured at gain_power time (see PlayerState.powers
        docstring), not a hardcoded table, so upgraded magnitudes are
        automatically correct."""
        for triggers in list(self.player.powers.values()) + list(self.player.temp_powers.values()):
            for eff in triggers.get(event, ()):
                if self.outcome is not None:
                    return
                self._apply_effect(eff, source=None, target=None)

    def _exhaust_card(self, card: CardInstance) -> None:
        """Unified exhaust entry point. Moves `card` into the exhaust pile
        (removing it from hand first if it's still there -- callers that
        already removed it, like play_card's self_exhausts routing, get a
        harmless no-op check), then fires both the 'any card exhausted'
        persistent-power hooks and this specific card's own
        on_exhaust_effects. Centralizing this matters because several
        different effects cause a card to be exhausted (self-exhaust on
        play, exhaust-a-random-card, exhaust-the-hand, ...) and none of them
        should be able to silently skip the on-exhaust triggers."""
        if card in self.player.hand:
            self.player.hand.remove(card)
        self.player.exhaust_pile.append(card)
        self.player.exhausted_a_card_this_turn = True
        self._fire_power_effects("on_exhaust")
        for eff in self.defs[card.card_id].on_exhaust_effects_for(card.is_upgraded):
            self._apply_effect(eff, card, None)

    def _check_condition(self, cond: str) -> bool:
        if cond == "exhausted_this_turn":
            return self.player.exhausted_a_card_this_turn
        if cond == "lost_hp_this_turn":
            return self.player.lost_hp_this_turn
        if cond == "exhaust_pile_size_gte_3":
            return len(self.player.exhaust_pile) >= 3
        raise ValueError(f"unknown condition: {cond}")

    # ------------------------------------------------------------------
    # legal actions
    # ------------------------------------------------------------------
    def legal_hand_indices(self) -> list[int]:
        if self.turn != "player" or self.outcome is not None:
            return []
        return [
            i
            for i, ci in enumerate(self.player.hand)
            if ci.cost(self.defs) <= self.player.energy
        ]

    def targets_for(self, hand_index: int) -> list[int] | None:
        """Returns list of legal enemy indices, or None if the card doesn't target a single enemy."""
        card = self.defs[self.player.hand[hand_index].card_id]
        if card.target != "single_enemy":
            return None
        return [i for i, e in enumerate(self.enemies) if e.alive]

    # ------------------------------------------------------------------
    # playing cards
    # ------------------------------------------------------------------
    def play_card(self, hand_index: int, target_index: int | None = None) -> None:
        if self.turn != "player" or self.outcome is not None:
            raise RuntimeError("cannot play a card outside the player's turn")
        ci = self.player.hand[hand_index]
        cost = ci.cost(self.defs)
        if cost > self.player.energy:
            raise ValueError("not enough energy")

        target_enemy = None
        if target_index is not None:
            target_enemy = self.enemies[target_index]
            if not target_enemy.alive:
                raise ValueError("target is not alive")

        self.player.energy -= cost
        for eff in ci.effects(self.defs):
            self._apply_effect(eff, ci, target_enemy)

        # Remove by identity (instance_id), not by the original positional
        # index: effects like exhaust_random_from_hand / draw can mutate
        # the hand's length/order before we get here, which would corrupt
        # a plain `del hand[hand_index]`. CardInstance's dataclass-derived
        # __eq__ includes the unique instance_id, so list.remove() finds
        # exactly this physical card even among duplicates of the same id.
        self.player.hand.remove(ci)
        if self.defs[ci.card_id].self_exhausts:
            self._exhaust_card(ci)  # already removed from hand above; no-ops the removal inside
        else:
            self.player.discard_pile.append(ci)
        self._check_combat_end()

    def _apply_effect(self, eff: dict, source: CardInstance | None, target: EnemyState | None) -> None:
        t = eff["type"]
        if t == "damage":
            for _ in range(eff.get("hits", 1)):
                if target is None or not target.alive:
                    break
                dmg = self._player_attack_damage(eff["amount"], target)
                self._damage_enemy(target, dmg)
        elif t == "damage_all":
            for _ in range(eff.get("hits", 1)):
                for e in self.alive_enemies():
                    dmg = self._player_attack_damage(eff["amount"], e)
                    self._damage_enemy(e, dmg)
        elif t == "damage_equals_block":
            if target is not None and target.alive:
                dmg = self._player_attack_damage(self.player.block, target)
                self._damage_enemy(target, dmg)
        elif t == "damage_double_if_target_vulnerable":
            hits = 2 if (target is not None and target.vulnerable > 0) else 1
            for _ in range(hits):
                if target is None or not target.alive:
                    break
                dmg = self._player_attack_damage(eff["amount"], target)
                self._damage_enemy(target, dmg)
        elif t == "damage_scales_with_tag":
            from .card import STRIKE_FAMILY_IDS

            count = sum(1 for c in self.whole_deck() if c.card_id in STRIKE_FAMILY_IDS)
            amount = eff["base"] + eff["per_tag"] * count
            if target is not None and target.alive:
                dmg = self._player_attack_damage(amount, target)
                self._damage_enemy(target, dmg)
        elif t == "damage_scales_with_pile_count":
            pile = getattr(self.player, eff["pile"])
            amount = eff["base"] + eff["per_card"] * len(pile)
            self._apply_effect({"type": "damage", "amount": amount, "hits": 1}, source, target)
        elif t == "damage_random_enemy":
            alive = self.alive_enemies()
            if alive:
                victim = self.rng.choice(alive)
                self._apply_effect({"type": "damage", "amount": eff["amount"], "hits": 1}, source, victim)
        elif t == "block":
            amount = eff["amount"]
            # Unmovable: the first time you gain Block from a card each
            # turn, double it. Simplification: v1 doesn't distinguish
            # "block from a card" vs. "block from a Power trigger" (e.g.
            # Crimson Mantle's turn-start block), so Unmovable can double a
            # Power-sourced block gain too if it happens to be the first one
            # that turn -- a rare Rare+Rare combo edge case, documented
            # rather than worth the extra plumbing to special-case.
            if "UNMOVABLE" in self.player.powers and not self.player.first_block_gain_used_this_turn:
                amount *= 2
                self.player.first_block_gain_used_this_turn = True
            gained = powers.compute_block_gain(amount, self.player.dexterity, self.player.frail)
            self.player.block += gained
            self._fire_power_effects("block_gained")
        elif t == "draw":
            if not self.player.no_draw_this_turn:
                self._draw_cards(eff["amount"])
        elif t == "draw_until_non_attack":
            if not self.player.no_draw_this_turn:
                for _ in range(MAX_HAND_SIZE):
                    if len(self.player.hand) >= MAX_HAND_SIZE:
                        break
                    if not self.player.draw_pile and not self.player.discard_pile:
                        break
                    self._draw_cards(1)
                    if self.defs[self.player.hand[-1].card_id].type != "Attack":
                        break
        elif t == "set_no_draw_this_turn":
            self.player.no_draw_this_turn = True
        elif t == "apply_vulnerable":
            if target is not None:
                target.vulnerable += eff["amount"]
                self._fire_power_effects("vulnerable_applied")
        elif t == "apply_vulnerable_all":
            for e in self.alive_enemies():
                e.vulnerable += eff["amount"]
            if self.alive_enemies():
                self._fire_power_effects("vulnerable_applied")
        elif t == "double_target_vulnerable":
            if target is not None:
                target.vulnerable *= 2
        elif t == "apply_weak":
            if target is not None:
                target.weak += eff["amount"]
        elif t == "enemy_lose_strength_this_turn":
            if target is not None:
                target.temp_strength_penalty -= eff["amount"]
        elif t == "gain_strength_this_turn":
            self.player.temp_strength += eff["amount"]
        elif t == "gain_strength":
            self.player.strength += eff["amount"]
        elif t == "gain_energy":
            self.player.energy += eff["amount"]
        elif t == "gain_power":
            self.player.powers[eff["power_id"]] = eff.get("triggers", {})
        elif t == "lose_hp":
            # "Lose HP" bypasses block entirely (distinct from taking damage).
            self.player.hp = max(0, self.player.hp - eff["amount"])
            self.player.lost_hp_this_turn = True
            self._check_combat_end()
            if self.outcome is None:
                self._fire_power_effects("hp_lost_own_turn")
        elif t == "conditional":
            branch = eff["then"] if self._check_condition(eff["if"]) else eff.get("else", [])
            for sub_eff in branch:
                self._apply_effect(sub_eff, source, target)
        elif t == "exhaust_random_from_hand":
            candidates = [c for c in self.player.hand if c is not source]
            if candidates:
                victim = self.rng.choice(candidates)
                self._exhaust_card(victim)
        elif t == "exhaust_hand_and_damage":
            hand_copy = [c for c in self.player.hand if c is not source]
            for c in hand_copy:
                self._exhaust_card(c)
            if hand_copy and target is not None and target.alive:
                amount = eff["per_card"] * len(hand_copy)
                self._apply_effect({"type": "damage", "amount": amount, "hits": 1}, source, target)
        elif t == "exhaust_hand_filtered_and_block":
            if eff["filter"] == "non_attack":
                hand_copy = [c for c in self.player.hand if c is not source and self.defs[c.card_id].type != "Attack"]
            else:
                raise ValueError(f"unknown exhaust_hand_filtered_and_block filter: {eff['filter']}")
            for c in hand_copy:
                self._exhaust_card(c)
            if hand_copy:
                amount = eff["per_card"] * len(hand_copy)
                self._apply_effect({"type": "block", "amount": amount}, source, target)
        elif t == "exhaust_random_attack_and_add_damage":
            attack_candidates = [
                c for c in self.player.hand if c is not source and self.defs[c.card_id].type == "Attack"
            ]
            bonus = 0
            if attack_candidates:
                victim = self.rng.choice(attack_candidates)
                for veff in self.defs[victim.card_id].effects_for(victim.is_upgraded):
                    if veff["type"] == "damage":
                        bonus += veff["amount"] * veff.get("hits", 1)
                self._exhaust_card(victim)
            total = eff["base"] + bonus
            self._apply_effect({"type": "damage", "amount": total, "hits": eff.get("hits", 1)}, source, target)
        elif t == "return_random_attack_from_discard_upgraded":
            attack_candidates = [c for c in self.player.discard_pile if self.defs[c.card_id].type == "Attack"]
            if attack_candidates and len(self.player.hand) < MAX_HAND_SIZE:
                card = self.rng.choice(attack_candidates)
                self.player.discard_pile.remove(card)
                card.is_upgraded = True
                self.player.hand.append(card)
        elif t == "upgrade_random_card_in_hand":
            candidates = [c for c in self.player.hand if c is not source and not c.is_upgraded]
            if candidates:
                n = len(candidates) if eff.get("all") else 1
                for c in self.rng.sample(candidates, min(n, len(candidates))):
                    c.is_upgraded = True
        elif t == "move_card_discard_to_draw_top":
            if self.player.discard_pile:
                card = self.rng.choice(self.player.discard_pile)
                self.player.discard_pile.remove(card)
                self.player.draw_pile.append(card)  # pile end == top (pop() draws from the end)
        else:
            raise ValueError(f"unknown effect type: {t}")

    # ------------------------------------------------------------------
    # turn structure
    # ------------------------------------------------------------------
    def end_turn(self) -> None:
        if self.turn != "player" or self.outcome is not None:
            raise RuntimeError("cannot end turn outside the player's turn")
        self.player.discard_pile.extend(self.player.hand)
        self.player.hand.clear()
        self.player.temp_strength = 0  # "this turn only" strength wears off here
        self.turn = "enemy"
        self._run_enemy_turn()
        if self.outcome is None:
            self._start_player_turn()

    def _run_enemy_turn(self) -> None:
        for enemy in self.alive_enemies():
            # debuffs on the enemy tick down at the start of the enemy's own turn
            if enemy.vulnerable > 0:
                enemy.vulnerable -= 1
            if enemy.weak > 0:
                enemy.weak -= 1
            enemy.block = 0  # v1 has no block-persistence relic (Barricade not modeled for enemies)

            intent = enemy.current_intent()
            if "buff_strength" in intent:
                enemy.strength += intent["buff_strength"]
            if "block" in intent:
                enemy.block += powers.compute_block_gain(intent["block"])
            if "debuff_player_weak" in intent:
                self.player.weak += intent["debuff_player_weak"]
            if "debuff_player_vulnerable" in intent:
                self.player.vulnerable += intent["debuff_player_vulnerable"]
            if "debuff_player_frail" in intent:
                self.player.frail += intent["debuff_player_frail"]
            if "attack" in intent:
                atk = intent["attack"]
                for _ in range(atk.get("hits", 1)):
                    if self.outcome is not None:
                        break
                    dmg = powers.compute_attack_damage(
                        atk["amount"], enemy.effective_strength, enemy.weak, self.player.vulnerable
                    )
                    self._damage_player(dmg)
                    self._check_combat_end()
                    if self.outcome is not None:
                        break
            enemy.turn_index += 1
            # Mangle-style "enemy loses N Strength this turn" penalties apply
            # to exactly this one enemy turn, then clear.
            enemy.temp_strength_penalty = 0
            if self.outcome is not None:
                break
        self._check_combat_end()

    def _start_player_turn(self) -> None:
        self.round_num += 1
        self.turn = "player"
        if "BARRICADE" not in self.player.powers:
            self.player.block = 0
        self.player.temp_powers.clear()
        self.player.exhausted_a_card_this_turn = False
        self.player.lost_hp_this_turn = False
        self.player.first_block_gain_used_this_turn = False
        self.player.no_draw_this_turn = False
        if self.player.vulnerable > 0:
            self.player.vulnerable -= 1
        if self.player.weak > 0:
            self.player.weak -= 1
        if self.player.frail > 0:
            self.player.frail -= 1
        self.player.energy = self.player.max_energy
        self._fire_power_effects("turn_start")
        if self.outcome is None:
            self._draw_cards(STARTING_HAND_SIZE - len(self.player.hand))

    # ------------------------------------------------------------------
    # end conditions
    # ------------------------------------------------------------------
    def _check_combat_end(self) -> None:
        if self.outcome is not None:
            return
        if self.player.hp <= 0:
            self.outcome = "loss"
        elif not self.alive_enemies():
            self.outcome = "win"
