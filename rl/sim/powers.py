"""Buff/debuff math for the Phase B v1 combat simulator.

Covers the mechanics explicitly scoped into v1 (see
C:\\Users\\ZacPC\\.claude\\plans\\radiant-spinning-marble.md): Strength,
Dexterity, Vulnerable, Weak, Frail. Formulas are calibrated against a real
value observed during the 2026-07-09 verification playthrough: an upgraded
Strike (9 base damage, no Strength) landing on a target with 1 stack of
Vulnerable dealt 13 damage -- i.e. floor(9 * 1.5) == 13. That fixes the
rounding rule (floor, applied after each percentage step) used below.
"""

from __future__ import annotations

import math


def strength_bonus(base_damage: int, strength: int) -> int:
    """Strength adds flat damage per hit."""
    return base_damage + strength


def apply_weak(damage: int, weak_stacks: int, percent: int = 25) -> int:
    """Weak reduces the *attacker's* outgoing damage by `percent` (floor)."""
    if weak_stacks <= 0:
        return damage
    return math.floor(damage * (1 - percent / 100))


def apply_vulnerable(damage: int, vulnerable_stacks: int, percent: int = 50) -> int:
    """Vulnerable increases damage the *target* takes by `percent` (floor).

    Calibration point: 9 base damage, 1 Vulnerable stack -> floor(9 * 1.5) == 13,
    matching the real Strike+/Bash combo observed live on 2026-07-09.
    """
    if vulnerable_stacks <= 0:
        return damage
    return math.floor(damage * (1 + percent / 100))


def apply_cruelty(damage: int, vulnerable_stacks: int, cruelty_percent: int) -> int:
    """Cruelty (Ironclad Power, Stage 2): Vulnerable enemies take an extra
    `cruelty_percent`% damage, on top of Vulnerable's own multiplier -- a
    second percent step, floored independently. `cruelty_percent` is 0 when
    Cruelty isn't active (base card is 25%, upgraded is 50% -- a real
    per-card magnitude, not a fixed constant, so the caller passes it in
    rather than this function assuming a default)."""
    if cruelty_percent <= 0 or vulnerable_stacks <= 0:
        return damage
    return math.floor(damage * (1 + cruelty_percent / 100))


def compute_attack_damage(
    base_damage: int,
    attacker_strength: int = 0,
    attacker_weak_stacks: int = 0,
    target_vulnerable_stacks: int = 0,
    cruelty_percent: int = 0,
) -> int:
    """Full per-hit damage pipeline: Strength (additive) -> Weak (attacker,
    percent) -> Vulnerable (target, percent) -> Cruelty (target, percent),
    each percentage step floored independently.
    """
    dmg = strength_bonus(base_damage, attacker_strength)
    dmg = apply_weak(dmg, attacker_weak_stacks)
    dmg = apply_vulnerable(dmg, target_vulnerable_stacks)
    dmg = apply_cruelty(dmg, target_vulnerable_stacks, cruelty_percent)
    return max(dmg, 0)


def dexterity_bonus(base_block: int, dexterity: int) -> int:
    """Dexterity adds flat block per block-granting card."""
    return base_block + dexterity


def apply_frail(block: int, frail_stacks: int, percent: int = 25) -> int:
    """Frail reduces block gained by `percent` (floor)."""
    if frail_stacks <= 0:
        return block
    return math.floor(block * (1 - percent / 100))


def compute_block_gain(base_block: int, dexterity: int = 0, frail_stacks: int = 0) -> int:
    block = dexterity_bonus(base_block, dexterity)
    block = apply_frail(block, frail_stacks)
    return max(block, 0)


def apply_damage_to_combatant(hp: int, block: int, incoming_damage: int) -> tuple[int, int]:
    """Block absorbs first, then HP. Returns (new_hp, new_block)."""
    if incoming_damage <= 0:
        return hp, block
    absorbed = min(block, incoming_damage)
    remaining = incoming_damage - absorbed
    new_block = block - absorbed
    new_hp = max(hp - remaining, 0)
    return new_hp, new_block
