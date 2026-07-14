"""Per-iteration diagnostics that make silent training bugs loud.

Two real bugs survived from M1 to v5 because nothing watched for them:

* ``use_potion`` was never once offered to the policy (``legal_actions`` read the
  wrong key), so the agent reached the Act 1 boss holding three potions it could
  not drink. An action-mix readout would have shown a flat zero from day one.
* Potential-based shaping left a non-zero potential on the ``game_over`` terminal
  but zero on a win, so dying at the boss out-returned beating it (+1.76 against
  +0.65). Nothing compared the return of a win against the return of a loss.

These are cheap to compute from the episode records we already collect, so they
are computed every iteration and surfaced rather than left for a human to notice.
"""
from __future__ import annotations

from collections import Counter
from typing import Sequence

from .ppo import EpisodeRecord


def episode_return(record: EpisodeRecord, gamma: float) -> float:
    """The discounted return PPO actually optimises for this episode."""
    return sum((gamma ** i) * step.reward for i, step in enumerate(record.steps))


def reward_health(records: Sequence[EpisodeRecord], gamma: float) -> dict[str, float | bool | None]:
    """Is winning worth more than losing?

    The single most important invariant in the whole reward function, and the one
    that was violated for the project's entire history. ``inverted`` true means
    the policy is being actively taught to lose.
    """
    wins = [episode_return(r, gamma) for r in records if r.outcome is True]
    losses = [episode_return(r, gamma) for r in records if r.outcome is False]
    win_mean = sum(wins) / len(wins) if wins else None
    loss_mean = sum(losses) / len(losses) if losses else None
    inverted = (
        win_mean is not None and loss_mean is not None and loss_mean >= win_mean
    )
    return {
        "win_return": None if win_mean is None else round(win_mean, 4),
        "loss_return": None if loss_mean is None else round(loss_mean, 4),
        "win_episodes": len(wins),
        "loss_episodes": len(losses),
        "inverted": inverted,
    }


def action_mix(records: Sequence[EpisodeRecord]) -> dict[str, float]:
    """Fraction of decisions spent on each action type.

    An action type stuck at exactly zero means the policy cannot reach it at all —
    which is how a whole resource (potions) stayed invisible for five versions.
    """
    counts: Counter[str] = Counter()
    for record in records:
        for step in record.steps:
            counts[step.candidates[step.index].action] += 1
    total = sum(counts.values())
    if not total:
        return {}
    return {action: round(n / total, 4) for action, n in counts.most_common()}


def offered_actions(records: Sequence[EpisodeRecord]) -> dict[str, int]:
    """How often each action type was even *available* to choose.

    Distinguishes "the policy dislikes potions" from "the policy was never shown a
    potion" — the difference between a preference and a bug.
    """
    counts: Counter[str] = Counter()
    for record in records:
        for step in record.steps:
            for candidate in step.candidates:
                counts[candidate.action] += 1
    return dict(counts.most_common())


def depth_profile(records: Sequence[EpisodeRecord], boss_floor: float = 15.0) -> dict[str, float | int]:
    """Where runs actually end, and how many convert once they get there.

    ``avg_floor`` alone hid the real story in v5: a mean of 14 looked like steady
    progress while it actually meant "62% of runs walk to the boss door and 0% of
    them win".
    """
    finished = [r for r in records if r.error is None]
    if not finished:
        return {}
    reached = [r for r in finished if r.outcome is True or r.final_floor >= boss_floor]
    wins = [r for r in finished if r.outcome is True]
    return {
        "episodes": len(finished),
        "reached_boss": len(reached),
        "reached_boss_rate": round(len(reached) / len(finished), 4),
        "boss_conversion": round(len(wins) / len(reached), 4) if reached else 0.0,
        "median_floor": sorted(r.final_floor for r in finished)[len(finished) // 2],
        "max_floor": max(r.final_floor for r in finished),
    }
