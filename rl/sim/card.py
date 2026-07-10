"""Card data model for the Phase B v1 combat simulator.

Loads card pools from rl/data/cards/<color>.json (one file per character
class -- ironclad.json is populated, silent/defect/necrobinder/regent.json
are Stage 3 placeholders per plan/plan.md's roadmap). Sourced from the
spire-codex live API (https://spire-codex.com, PolyForm Noncommercial
1.0.0, reverse-engineered from the game's own decompiled data) -- see each
file's `_meta` block and plan/plan.md's 2026-07-10 entries for full
provenance and the noncommercial license terms. This module only defines
the data model; effect *execution* lives in combat.py, which has access
to the full battle state effects need (player/enemy HP, block, powers,
piles).
"""

from __future__ import annotations

import itertools
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CARDS_DIR = DATA_DIR / "cards"
# v1's default combined pool is Ironclad-only. Other classes (silent/defect/
# necrobinder/regent) live in cards/<color>.json as placeholders until
# Stage 3 of the roadmap (plan/plan.md 2026-07-10 entry) converts them --
# callers who want a specific class or a merged multi-class pool pass their
# own `paths` list.
DEFAULT_CARD_PATHS: tuple[Path, ...] = (CARDS_DIR / "ironclad.json",)

# Cards tagged "Strike" in spire-codex's own data (ground truth, not a
# guess) -- this is the family PERFECTED_STRIKE's damage_scales_with_tag
# effect counts. Note ULTIMATE_STRIKE is NOT part of this family despite
# its English name containing "Strike" -- confirmed by the real tags data,
# correcting an earlier hand-curated guess that had included it.
STRIKE_FAMILY_IDS: frozenset[str] = frozenset(
    {
        "STRIKE_IRONCLAD",
        "TWIN_STRIKE",
        "PERFECTED_STRIKE",
        "SETUP_STRIKE",
        "POMMEL_STRIKE",
    }
)


@dataclass(frozen=True)
class CardDef:
    id: str
    name: str
    type: str  # "Attack" | "Skill"
    rarity: str
    cost: int
    target: str  # "single_enemy" | "self" | "all_enemies"
    effects: tuple[dict, ...]
    upgraded_cost: int
    effects_upgraded: tuple[dict, ...]
    self_exhausts: bool = False
    note: str | None = None
    # Fires whenever a copy of THIS card is exhausted, regardless of cause
    # (self-exhaust, another card's exhaust-a-card effect, etc.) -- distinct
    # from a persistent Power's "whenever any card is exhausted" trigger.
    # See Combat._exhaust_card() in combat.py. Stage 2, e.g. Drum of Battle
    # (whose on-exhaust Energy gain itself scales with upgrade, hence the
    # separate _upgraded variant mirroring effects/effects_upgraded).
    on_exhaust_effects: tuple[dict, ...] = ()
    on_exhaust_effects_upgraded: tuple[dict, ...] = ()

    def cost_for(self, is_upgraded: bool) -> int:
        return self.upgraded_cost if is_upgraded else self.cost

    def effects_for(self, is_upgraded: bool) -> tuple[dict, ...]:
        return self.effects_upgraded if is_upgraded else self.effects

    def on_exhaust_effects_for(self, is_upgraded: bool) -> tuple[dict, ...]:
        return self.on_exhaust_effects_upgraded if is_upgraded else self.on_exhaust_effects

    def is_in_strike_family(self) -> bool:
        return self.id in STRIKE_FAMILY_IDS


def load_card_defs(paths: Iterable[Path] = DEFAULT_CARD_PATHS) -> dict[str, CardDef]:
    """Loads and merges one or more cards/<color>.json files. Later paths
    win on id collisions (shouldn't happen between distinct class files)."""
    defs: dict[str, CardDef] = {}
    for path in paths:
        raw = json.loads(path.read_text(encoding="utf-8"))
        for entry in raw["cards"]:
            defs[entry["id"]] = CardDef(
                id=entry["id"],
                name=entry["name"],
                type=entry["type"],
                rarity=entry["rarity"],
                cost=entry["cost"],
                target=entry["target"],
                effects=tuple(entry["effects"]),
                upgraded_cost=entry["upgraded_cost"],
                effects_upgraded=tuple(entry["effects_upgraded"]),
                self_exhausts=entry.get("self_exhausts", False),
                note=entry.get("note"),
                on_exhaust_effects=tuple(entry.get("on_exhaust_effects", ())),
                on_exhaust_effects_upgraded=tuple(entry.get("on_exhaust_effects_upgraded", ())),
            )
    return defs


_instance_id_counter = itertools.count(1)


@dataclass
class CardInstance:
    """A specific physical copy of a card sitting in some pile/hand."""

    instance_id: int = field(default_factory=lambda: next(_instance_id_counter))
    card_id: str = ""
    is_upgraded: bool = False

    def cost(self, defs: dict[str, CardDef]) -> int:
        return defs[self.card_id].cost_for(self.is_upgraded)

    def effects(self, defs: dict[str, CardDef]) -> tuple[dict, ...]:
        return defs[self.card_id].effects_for(self.is_upgraded)

    def display_name(self, defs: dict[str, CardDef]) -> str:
        base = defs[self.card_id].name
        return f"{base}+" if self.is_upgraded else base


def make_starter_deck() -> list[CardInstance]:
    """Ironclad's real starting deck: 5x Strike, 4x Defend, 1x Bash."""
    deck = [CardInstance(card_id="STRIKE_IRONCLAD") for _ in range(5)]
    deck += [CardInstance(card_id="DEFEND_IRONCLAD") for _ in range(4)]
    deck += [CardInstance(card_id="BASH")]
    return deck
