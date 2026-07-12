"""M2 curriculum stages: which combats (or runs) an episode samples from.

Stages are data, not behavior: a stage either fixes an encounter pool for
atomic ``start_combat`` resets, or marks full-run mode (``encounters=()``)
where episodes go through ``start_run``. Sampling is deterministic in the
episode seed so every experiment is replayable from its seed list alone.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Sequence

from .engine import CombatConfig, RunConfig

IRONCLAD_STARTING_DECK: tuple[str, ...] = (
    ("STRIKE_IRONCLAD",) * 5 + ("DEFEND_IRONCLAD",) * 4 + ("BASH",)
)


@dataclass(frozen=True)
class CurriculumStage:
    """One rung of the ladder. ``encounters`` empty means full-run episodes."""

    name: str
    encounters: tuple[str, ...] = ()
    max_act: int | None = None  # full-run mode: stop after finishing this act

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


def episode_config(
    stage: CurriculumStage, seed: str, *, character: str = "Ironclad", ascension: int = 0
) -> CombatConfig | RunConfig:
    if stage.is_combat:
        return CombatConfig(
            character, seed, sample_encounter(stage, seed), ascension=ascension,
            deck=IRONCLAD_STARTING_DECK if character == "Ironclad" else None,
            hp=sample_starting_hp(stage, seed), max_hp=80,
        )
    return RunConfig(character, seed, ascension)


def ironclad_stages(encounter_catalog: Sequence[dict]) -> tuple[CurriculumStage, ...]:
    """Build the four M2 stages from the engine's ``list_models`` encounter rows.

    normal combat -> mixed combat (regulars + elites) -> Act 1 runs -> full A0.
    """
    def act1(category: str) -> tuple[str, ...]:
        return tuple(sorted(
            str(row["id"]) for row in encounter_catalog
            if row["act"] == 1 and row["category"] == category
        ))

    weak, regular, elite = act1("weak"), act1("regular"), act1("elite")
    if not (weak and regular and elite):
        raise ValueError("encounter catalog is missing act 1 categories")
    return (
        CurriculumStage("normal_combat", weak + regular),
        CurriculumStage("mixed_combat", weak + regular + elite),
        CurriculumStage("act1", max_act=1),
        CurriculumStage("full_a0"),
    )
