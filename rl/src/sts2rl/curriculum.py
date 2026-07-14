"""M2 curriculum stages: which combats (or runs) an episode samples from.

Stages are data, not behavior: a stage either fixes an encounter pool for
atomic ``start_combat`` resets, or marks full-run mode (``encounters=()``)
where episodes go through ``start_run``. Sampling is deterministic in the
episode seed so every experiment is replayable from its seed list alone.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any, Mapping, Sequence

from .engine import CombatConfig, RunConfig

IRONCLAD_STARTING_DECK: tuple[str, ...] = (
    ("STRIKE_IRONCLAD",) * 5 + ("DEFEND_IRONCLAD",) * 4 + ("BASH",)
)


@dataclass(frozen=True)
class Loadout:
    """A mid-run player snapshot used to make bridge combats realistic."""

    hp: int
    max_hp: int
    deck: tuple[str, ...]
    relics: tuple[str, ...]
    potions: tuple[str, ...]
    encounter: str | None = None


@dataclass(frozen=True)
class CurriculumStage:
    """One rung of the ladder. ``encounters`` empty means full-run episodes."""

    name: str
    encounters: tuple[str, ...] = ()
    max_act: int | None = None  # full-run mode: stop after finishing this act
    loadouts: tuple[Loadout, ...] = ()  # bridge combats: harvested mid-run snapshots

    @property
    def is_combat(self) -> bool:
        return bool(self.encounters)


def sample_encounter(stage: CurriculumStage, seed: str) -> str:
    """Deterministic encounter choice: same stage + seed, same combat."""
    if not stage.is_combat:
        raise ValueError(f"stage {stage.name!r} is full-run, not combat")
    digest = hashlib.sha256(f"m2-curriculum:{stage.name}:{seed}".encode()).digest()
    return stage.encounters[int.from_bytes(digest[:8], "big") % len(stage.encounters)]


COMBAT_HP_RANGE = (25, 80)


def sample_starting_hp(stage: CurriculumStage, seed: str) -> int:
    """Combat episodes start at a seed-determined HP, not always full.

    Full runs carry HP between fights; a policy trained only from full HP
    never learns that blocking is a cross-fight resource and dies to chip
    damage mid-run (observed: floor 6-9 deaths against weak monsters that
    the same policy beats 96% of the time from full HP).
    """
    low, high = COMBAT_HP_RANGE
    digest = hashlib.sha256(f"m2-curriculum-hp:{stage.name}:{seed}".encode()).digest()
    return low + int.from_bytes(digest[:8], "big") % (high - low + 1)


def sample_loadout(stage: CurriculumStage, seed: str) -> Loadout:
    digest = hashlib.sha256(f"m2-curriculum-loadout:{stage.name}:{seed}".encode()).digest()
    return stage.loadouts[int.from_bytes(digest[:8], "big") % len(stage.loadouts)]


def episode_config(
    stage: CurriculumStage, seed: str, *, character: str = "Ironclad", ascension: int = 0
) -> CombatConfig | RunConfig:
    if stage.is_combat and stage.loadouts:
        loadout = sample_loadout(stage, seed)
        encounter = (
            loadout.encounter if loadout.encounter in stage.encounters
            else sample_encounter(stage, seed)
        )
        return CombatConfig(
            character, seed, encounter, ascension=ascension,
            deck=loadout.deck, relics=loadout.relics, potions=loadout.potions,
            hp=loadout.hp, max_hp=loadout.max_hp,
        )
    if stage.is_combat:
        return CombatConfig(
            character, seed, sample_encounter(stage, seed), ascension=ascension,
            deck=IRONCLAD_STARTING_DECK if character == "Ironclad" else None,
            hp=sample_starting_hp(stage, seed), max_hp=80,
        )
    return RunConfig(character, seed, ascension)


def boss_loadout_from_state(state: Mapping[str, Any]) -> Loadout | None:
    """Extract the exact on-policy snapshot at the start of an Act boss fight."""
    context = state.get("context") or {}
    if (state.get("decision") != "combat_play"
            or context.get("room_type") != "Boss"
            or int(state.get("round", 0) or 0) != 1):
        return None
    boss = context.get("boss") or {}
    encounter = boss.get("id")
    player = state.get("player") or {}
    deck = []
    for card in player.get("deck") or []:
        card_id = str(card.get("id") or "")
        if not card_id:
            continue
        card_id = card_id.removeprefix("CARD.")
        if card.get("upgraded"):
            card_id += "+"
        deck.append(card_id)
    if not encounter or not deck or int(player.get("hp", 0) or 0) <= 0:
        return None
    return Loadout(
        hp=int(player["hp"]), max_hp=int(player.get("max_hp", 80) or 80),
        deck=tuple(deck),
        relics=tuple(
            str(row["id"]).removeprefix("RELIC.")
            for row in player.get("relics") or [] if row.get("id")
        ),
        potions=tuple(
            str(row["id"]).removeprefix("POTION.")
            for row in player.get("potions") or [] if row.get("id")
        ),
        encounter=str(encounter),
    )


def boss_replay_split(seeds: Sequence[str], fraction: float) -> tuple[list[str], list[str]]:
    """Split an iteration's seeds into (boss replay, main stage).

    A full run holds one boss fight and only ~60% of runs reach it, so the boss
    gradient is diluted ~1:100 and the skill decays during run-stage training.
    Replaying a fixed slice of each iteration as boss fights holds it in place.
    """
    if not 0.0 <= fraction < 1.0:
        raise ValueError(f"fraction must be in [0, 1): {fraction}")
    count = round(fraction * len(seeds))
    return list(seeds[:count]), list(seeds[count:])


def act_variant_of(encounter_catalog: Sequence[dict], encounter_id: str) -> str | None:
    """The ``act_id`` (region) an encounter belongs to, e.g. ``OVERGROWTH``."""
    for row in encounter_catalog:
        if str(row.get("id")) == encounter_id:
            return None if row.get("act_id") is None else str(row["act_id"])
    return None


def ironclad_stages(
    encounter_catalog: Sequence[dict], boss_loadouts: Sequence[Loadout] = (),
    *, act_variant: str | None = None,
) -> tuple[CurriculumStage, ...]:
    """Build the M2 stages from the engine's ``list_models`` encounter rows.

    normal combat -> mixed combat (regulars + elites) -> [boss bridge with
    harvested mid-run loadouts, when available] -> Act 1 runs -> full A0.

    ``act_variant`` restricts the encounter pools to one region. Act 1 ships two
    disjoint regions (OVERGROWTH / UNDERDOCKS) but a run only ever visits one of
    them — 300/300 sampled A0 Ironclad runs start in Overgrowth — so leaving both
    in spends ~48% of every combat stage on monsters the agent will never meet,
    and calibrates the advance thresholds against that polluted mix. Callers pass
    the region the engine actually hands out (see ``m2_train.detect_act_variant``).
    ``None`` keeps the full pool.
    """
    def act1(category: str) -> tuple[str, ...]:
        return tuple(sorted(
            str(row["id"]) for row in encounter_catalog
            if row["act"] == 1 and row["category"] == category
            and (act_variant is None or str(row.get("act_id")) == act_variant)
        ))

    weak, regular, elite, boss = act1("weak"), act1("regular"), act1("elite"), act1("boss")
    if not (weak and regular and elite and boss):
        raise ValueError(
            f"encounter catalog is missing act 1 categories (act_variant={act_variant!r})"
        )
    stages = [
        CurriculumStage("normal_combat", weak + regular),
        CurriculumStage("mixed_combat", weak + regular + elite),
    ]
    if boss_loadouts:
        # Boss-only: elites are already covered by mixed_combat, and averaging
        # them in masked near-zero boss win rates behind a passing stage score.
        stages.append(CurriculumStage("boss_combat", boss, loadouts=tuple(boss_loadouts)))
    stages.append(CurriculumStage("act1", max_act=1))
    stages.append(CurriculumStage("full_a0"))
    return tuple(stages)
