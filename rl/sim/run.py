"""Stage 2.5: run-continuity state machine, layered on top of sim/combat.py.

A `Run` is a sequence of `FIGHTS_PER_RUN` back-to-back Combats where HP is
NOT reset between fights (it carries over, minus whatever damage was taken)
and the deck grows over the run via a simple reward-card pick after each
win. This is a deliberately minimal version of "run continuity" -- no map
choice (the fight sequence is just N random encounters), no shop/events/
relics/potions (still deferred to the full Stage 6). It exists specifically
to fix two things v1/Stage 1/Stage 2's single-combat scope couldn't: the
deck never evolved, and every fight started at full HP regardless of how
the previous one went -- see plan/stage2.5_run_continuity.md for the design
rationale.

`Combat` itself is untouched by this -- `Run` just orchestrates a sequence
of `Combat` instances, carrying `hp` and `deck` across the boundary.
"""

from __future__ import annotations

import random

from .card import CardDef, CardInstance, load_card_defs, make_starter_deck
from .combat import Combat, load_monster_data

FIGHTS_PER_RUN = 5
REWARD_CHOICES = 3


class Run:
    """One mini-run: FIGHTS_PER_RUN Combats back to back, HP carried over,
    deck grows via a 3-card reward pick after each non-final win.

    `phase` is "combat" (delegate actions to the current Combat) or
    "reward_pick" (choose one of `pending_reward_choices`, or skip).
    `outcome` is None while the run is ongoing, else "run_won"/"run_lost".
    """

    def __init__(
        self,
        card_defs: dict[str, CardDef] | None = None,
        monster_data: dict | None = None,
        rng: random.Random | None = None,
        player_max_hp: int = 80,
        fights_per_run: int = FIGHTS_PER_RUN,
    ):
        self.defs = card_defs or load_card_defs()
        self.monster_data = monster_data or load_monster_data()
        self.rng = rng or random.Random()
        self.player_max_hp = player_max_hp
        self.fights_per_run = fights_per_run

        # Real STS reward pools don't offer the Basic-rarity starter cards
        # (Strike/Defend/Bash) -- only Common/Uncommon/Rare/Ancient cards
        # show up as run rewards. Mirrored here for the same reason.
        self.reward_pool_ids = [cid for cid, d in self.defs.items() if d.rarity != "Basic"]

        self.deck: list[CardInstance] = make_starter_deck()
        self.hp = player_max_hp
        self.fight_index = 0  # 0-based
        self.phase = "combat"
        self.outcome: str | None = None
        self.pending_reward_choices: list[str] | None = None

        encounter_names = list(self.monster_data["encounters"].keys())
        self.encounter_sequence = [self.rng.choice(encounter_names) for _ in range(fights_per_run)]

        self.combat: Combat = self._start_combat()

    # ------------------------------------------------------------------
    def _start_combat(self) -> Combat:
        enemy_ids = self.monster_data["encounters"][self.encounter_sequence[self.fight_index]]["enemies"]
        combat = Combat(
            deck=self.deck,
            enemy_ids=enemy_ids,
            card_defs=self.defs,
            monster_data=self.monster_data,
            rng=self.rng,
            player_max_hp=self.player_max_hp,
        )
        # Combat.__init__ always starts a fresh PlayerState at full HP --
        # override with the run's carried-over HP for every fight after the
        # first (the first fight already starts full since self.hp ==
        # player_max_hp at construction time).
        combat.player.hp = self.hp
        return combat

    def _sync_deck_from_combat(self) -> None:
        """Pulls the deck's current composition (including any upgrades
        applied mid-fight) back out of the just-finished Combat's piles,
        so the next fight's shuffle sees the up-to-date deck."""
        self.deck = list(self.combat.whole_deck())

    def _after_combat_ended(self) -> None:
        # No separate "just finished" flag needed: `self.combat` keeps
        # referencing the just-ended Combat (outcome != None) until
        # pick_reward()/skip_reward() replaces it via _advance_to_next_fight,
        # so callers can detect "a fight just ended" by checking
        # `run.combat.outcome is not None` right after play_card()/end_turn().
        self._sync_deck_from_combat()
        self.hp = self.combat.player.hp

        if self.combat.outcome == "loss":
            self.outcome = "run_lost"
            return

        # win
        if self.fight_index == self.fights_per_run - 1:
            self.outcome = "run_won"
            return

        self.phase = "reward_pick"
        self.pending_reward_choices = self.rng.sample(
            self.reward_pool_ids, min(REWARD_CHOICES, len(self.reward_pool_ids))
        )

    # ------------------------------------------------------------------
    # combat-phase actions (delegate straight to Combat, then check for
    # fight-end transitions)
    # ------------------------------------------------------------------
    def play_card(self, hand_index: int, target_index: int | None = None) -> None:
        if self.phase != "combat" or self.outcome is not None:
            raise RuntimeError("play_card is only legal during the combat phase of an ongoing run")
        self.combat.play_card(hand_index, target_index)
        if self.combat.outcome is not None:
            self._after_combat_ended()

    def end_turn(self) -> None:
        if self.phase != "combat" or self.outcome is not None:
            raise RuntimeError("end_turn is only legal during the combat phase of an ongoing run")
        self.combat.end_turn()
        if self.combat.outcome is not None:
            self._after_combat_ended()

    # ------------------------------------------------------------------
    # reward-pick phase actions
    # ------------------------------------------------------------------
    def pick_reward(self, slot_index: int) -> None:
        if self.phase != "reward_pick" or self.outcome is not None:
            raise RuntimeError("pick_reward is only legal during the reward_pick phase")
        if not (0 <= slot_index < len(self.pending_reward_choices)):
            raise ValueError("slot_index out of range for pending_reward_choices")
        chosen_id = self.pending_reward_choices[slot_index]
        self.deck.append(CardInstance(card_id=chosen_id))
        self._advance_to_next_fight()

    def skip_reward(self) -> None:
        if self.phase != "reward_pick" or self.outcome is not None:
            raise RuntimeError("skip_reward is only legal during the reward_pick phase")
        self._advance_to_next_fight()

    def _advance_to_next_fight(self) -> None:
        self.pending_reward_choices = None
        self.fight_index += 1
        self.phase = "combat"
        self.combat = self._start_combat()
