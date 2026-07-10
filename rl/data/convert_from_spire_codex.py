"""One-off conversion script: spire-codex API data -> rl/data/cards/<color>.json + rl/data/monsters/<region>.json

Source: https://github.com/ptrlrd/spire-codex (live API at spire-codex.com),
licensed PolyForm Noncommercial 1.0.0 by Peter Lord and Spire Codex
contributors. Data reverse-engineered from Slay the Spire 2 game files.
NONCOMMERCIAL USE ONLY per that license -- see plan/plan.md's 2026-07-10
entry for the full provenance/licensing discussion and the decision to use
it. Required Notice per the license is embedded in every output file's
_meta block.

Run once from rl/data/: `python convert_from_spire_codex.py`. Reads the
_codex_*_raw.json snapshots already fetched via the live API and writes
the final curated JSON files consumed by sim/card.py and sim/combat.py.

v1/Stage 1 kept a restricted mechanic set: damage, block, draw, Strength,
Dexterity, Vulnerable, Weak, Frail, plus energy gain, direct HP loss, and
permanent vs. this-turn-only Strength. As of Stage 2 (plan/plan.md's
2026-07-10 entry), sim/combat.py also has a data-driven triggered-Power
system (turn-start / on-exhaust / on-HP-lost-this-turn / on-block-gained /
on-Vulnerable-applied hooks) and a generic `conditional` effect wrapper --
STAGE2_POWER_CARDS and STAGE2_COMPLEX_CARDS below are the 27 Ironclad cards
that system unlocks. Still NOT modeled: recursive auto-play (Stampede/Havoc/
Hellraiser/Howl from Beyond), dynamic card costs (Corruption/Stomp),
card-instance-persistent state (Rampage), "next card played" pending
modifiers (One Two Punch/Unrelenting/Rage), card-spawning effects (Anger/
Stoke/Infernal Blade), RandomEnemy/AnyAlly targeting, or X-cost cards --
see plan/plan.md's Stage 2 entry for the full "why not" per mechanic.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent

ALLOWED_POWERS = {"Strength", "Vulnerable", "Weak", "Frail", "Dexterity"}
POWER_TAG_RE = re.compile(r"\[gold\]([^\[]+?)\[/gold\]")


def load(name: str):
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


def mentioned_gold_tags(description: str) -> set[str]:
    return set(POWER_TAG_RE.findall(description or ""))


def apply_upgrade_diffs(vars_: dict, upgrade: dict | None) -> dict:
    if not upgrade:
        return dict(vars_)
    out = dict(vars_)
    lower_map = {k.lower(): k for k in vars_}
    for key, delta in upgrade.items():
        if key in ("cost",):
            continue
        match = lower_map.get(key.lower())
        if match is None:
            continue
        try:
            out[match] = vars_[match] + int(delta)
        except (TypeError, ValueError):
            pass
    return out


# Cards with a bespoke mechanic beyond the generic numeric-field extraction,
# but simple enough that we already built + tested the primitive for them
# in sim/combat.py (exhaust_random_from_hand / upgrade_random_card_in_hand /
# move_card_discard_to_draw_top). Everything else that touches the
# exhaust pile, draw pile, reactive end-of-turn triggers, or conditional
# scaling on exhaust-pile size is excluded from v1 as "too complex" --
# matching the approved plan's "skip high-complexity random-effect cards"
# scope, now applied consistently across the full 87-card pool instead of
# just the original hand-picked 18.
# Stage 2: the 27 Ironclad cards the triggered-Power system unlocks (see
# plan/plan.md's 2026-07-10 Stage 2 entry for the mechanism -> card mapping).
# Split into two sets for documentation clarity even though both are treated
# identically below (both need bespoke build_effects() branches and are
# exempt from the Power-type / complex-mechanic-text exclusion checks).
STAGE2_POWER_CARDS = {
    "AGGRESSION", "BARRICADE", "CRIMSON_MANTLE", "CRUELTY", "DARK_EMBRACE",
    "DEMON_FORM", "FEEL_NO_PAIN", "INFERNO", "JUGGERNAUT", "PYRE", "RUPTURE",
    "TANK", "UNMOVABLE", "VICIOUS",
}
STAGE2_COMPLEX_CARDS = {
    "ASHEN_STRIKE", "BATTLE_TRANCE", "DRUM_OF_BATTLE", "EVIL_EYE", "FIEND_FIRE",
    "FORGOTTEN_RITUAL", "MANGLE", "MOLTEN_FIST", "PACTS_END", "PILLAGE",
    "SECOND_WIND", "SPITE", "THRASH",
}
SIMPLE_BESPOKE_CARDS = {"ARMAMENTS", "TRUE_GRIT", "HEADBUTT"} | STAGE2_POWER_CARDS | STAGE2_COMPLEX_CARDS
COMPLEX_MECHANIC_PATTERN = re.compile(
    r"Exhaust Pile|when this card is \[gold\]Exhausted|at the end of your turn|"
    r"top card of your \[gold\]Draw Pile|add its damage|random card into your \[gold\]Hand|"
    r"free to play|3 or more cards|for each card \[gold\]Exhausted|"
    r"if you (have |lost |gained |played |took )|this turn,|"
    # Found by manual audit of the first automated pass -- each of these
    # is a real mechanic (card self-duplication, per-stack scaling,
    # doubling an existing debuff, draw-until, dynamic cost reduction,
    # "next card" discounts, on-kill bonuses, temporary enemy debuffs)
    # that isn't representable by the numeric-field extraction below, and
    # every one of them was silently *dropped* (not errored) by the first
    # pass rather than raising -- hence the belt-and-suspenders
    # unconsumed-vars check further down too.
    r"copy of this card|[Dd]ouble the enemy|until you draw|"
    r"[Cc]osts? \d+ less|[Cc]osts? 0 |[Nn]ext (Attack|card) you play|"
    r"[Ii]f \[gold\]Fatal|loses? \d+ \[gold\]Strength|this card's damage|"
    # "Exhaust N card(s)" (this card sends OTHER hand cards to the exhaust
    # pile) is a distinct mechanic from the "self_exhausts" keyword (this
    # card exhausts itself) and both use the same "[gold]Exhaust[/gold]"
    # tag, which is on the ignored non-power-tag allowlist below -- so it's
    # invisible to both the tag-based unsupported-power check and the
    # unconsumed-var check (the count is a hardcoded literal in the text,
    # never a vars entry). Found via manual spot-check of BRAND/CINDER
    # against their source_description after the vars-based checks passed
    # them clean. When phrased "at random" it's the same primitive TRUE_GRIT
    # already uses (exhaust_random_from_hand) and is handled generically
    # below instead of excluded here; without "at random" it's a
    # player-choice targeting mechanic v1 doesn't support, so it stays
    # excluded (e.g. BRAND, BURNING_PACT).
    r"Exhaust\[/gold\] \d+ card(?! at random)|"
    # "cannot draw additional cards" (e.g. Battle Trance) is a turn-scoped
    # restriction with no numeric var backing it at all (it's a boolean
    # text-only clause), so it's invisible to the unconsumed-var check too.
    # v1's approved status-effect set is Strength/Vulnerable/Weak/Frail/
    # Dexterity only -- a "no draw this turn" flag isn't in it, so exclude
    # rather than silently drop the restriction.
    r"cannot draw additional cards",
    re.IGNORECASE,
)
EXHAUST_RANDOM_FROM_HAND_RE = re.compile(r"Exhaust\[/gold\] \d+ card at random", re.IGNORECASE)
# Var keys that are redundant duplicates of an "XPower"-style key we already
# read (spire-codex's parser emits both, e.g. Inflame has both
# "StrengthPower": 2 and "Strength": 2 pointing at the same number), or are
# purely-derived convenience fields equal to a combination of others we do
# read. Excluded from the "did we miss something" check below.
IGNORE_VAR_KEYS = {"Strength", "Vulnerable", "Weak", "Frail", "Dexterity", "CalculatedDamage"}


# v1's conversion pipeline (mechanic denylist, unconsumed-var check, etc.)
# was only built and audited against Ironclad. Other classes go through the
# same code path but haven't had a human read every excluded/included card
# against its source_description yet, and likely need class-specific bespoke
# handling (Defect's orbs, Silent's poison stacking, Necrobinder/Regent's
# unknown-to-us mechanics) before their output should be trusted -- see
# Stage 3 of plan/plan.md's roadmap. Only run audited classes by default.
AUDITED_CARD_COLORS = {"ironclad"}
ALL_CARD_COLORS = ["ironclad", "silent", "defect", "necrobinder", "regent"]


def convert_cards(color: str) -> dict:
    all_cards = load("_codex_cards_raw.json")
    power_names = {p["name"] for p in load("_codex_powers_raw.json")}
    non_power_gold_tags = {"Block", "Exhausted", "Exhaust", "HP", "Innate", "Ethereal", "Retain"}

    out_cards = []
    excluded = []

    for c in all_cards:
        if c["color"] != color:
            continue
        if c["type"] not in ("Attack", "Skill", "Power"):
            continue

        reasons = []
        if c["type"] == "Power" and c["id"] not in ({"INFLAME"} | STAGE2_POWER_CARDS):
            reasons.append("Power-type card with persistent/reactive behavior needing the triggered-power system (deferred to a future stage beyond Stage 2's initial 14 power cards)")
        if c.get("target") in ("RandomEnemy", "AnyAlly"):
            reasons.append(f"unsupported target type {c.get('target')}")
        if c.get("is_x_cost") or c.get("is_x_star_cost"):
            reasons.append("X-cost card (dynamic cost resolution not implemented in v1)")
        if c.get("spawns_cards"):
            reasons.append("spawns new card instances at runtime (not implemented in v1)")
        if c["id"] not in SIMPLE_BESPOKE_CARDS and COMPLEX_MECHANIC_PATTERN.search(
            c.get("description") or ""
        ):
            reasons.append("complex exhaust-pile/draw-pile/reactive-trigger mechanic (deferred to v2)")

        desc = c.get("description") or ""
        gold_tags = mentioned_gold_tags(desc)
        mentioned_powers = {t for t in gold_tags if t in power_names}
        unsupported_powers = mentioned_powers - ALLOWED_POWERS - non_power_gold_tags
        if unsupported_powers:
            reasons.append(f"uses unsupported power(s): {sorted(unsupported_powers)}")

        if reasons:
            excluded.append({"id": c["id"], "name": c["name"], "reasons": reasons})
            continue

        upgraded_vars = apply_upgrade_diffs(c.get("vars") or {}, c.get("upgrade"))
        consumed_var_keys: set[str] = set()

        def v(base_field, var_name, upgraded=False):
            src = upgraded_vars if upgraded else (c.get("vars") or {})
            if var_name in src:
                consumed_var_keys.add(var_name)
                return src[var_name]
            return c.get(base_field)

        target_map = {"AnyEnemy": "single_enemy", "AllEnemies": "all_enemies", "Self": "self"}
        target = target_map[c["target"]]

        def build_effects(upgraded: bool) -> list[dict]:
            effects = []
            cid = c["id"]

            if cid == "BODY_SLAM":
                effects.append({"type": "damage_equals_block"})
                return effects
            if cid == "PERFECTED_STRIKE":
                base = v("damage", "CalculationBase", upgraded)
                per_tag = v(None, "ExtraDamage", upgraded)
                effects.append(
                    {"type": "damage_scales_with_tag", "base": base, "per_tag": per_tag, "tag": "Strike"}
                )
                return effects
            if cid == "DISMANTLE":
                effects.append(
                    {"type": "damage_double_if_target_vulnerable", "amount": v("damage", "Damage", upgraded)}
                )
                return effects
            if cid == "ARMAMENTS":
                effects.append({"type": "block", "amount": v("block", "Block", upgraded)})
                effects.append({"type": "upgrade_random_card_in_hand", "all": upgraded})
                return effects
            if cid == "TRUE_GRIT":
                effects.append({"type": "block", "amount": v("block", "Block", upgraded)})
                effects.append({"type": "exhaust_random_from_hand"})
                return effects
            if cid == "HEADBUTT":
                effects.append({"type": "damage", "amount": v("damage", "Damage", upgraded), "hits": 1})
                effects.append({"type": "move_card_discard_to_draw_top"})
                return effects

            # --- Stage 2: triggered-Power cards (gain_power + triggers) ---
            # Magnitudes are read via v() from the card's own base/upgraded
            # vars, not hardcoded, so e.g. Demon Form+ correctly grants 3
            # Strength/turn instead of 2 -- see plan/plan.md's Stage 2 entry
            # for why the trigger magnitude has to live on the granted power
            # itself rather than a static lookup table.
            if cid == "AGGRESSION":
                effects.append(
                    {
                        "type": "gain_power",
                        "power_id": "AGGRESSION",
                        "triggers": {"turn_start": [{"type": "return_random_attack_from_discard_upgraded"}]},
                    }
                )
                return effects
            if cid == "BARRICADE":
                effects.append({"type": "gain_power", "power_id": "BARRICADE"})
                return effects
            if cid == "CRIMSON_MANTLE":
                block_amt = v(None, "CrimsonMantlePower", upgraded)
                effects.append(
                    {
                        "type": "gain_power",
                        "power_id": "CRIMSON_MANTLE",
                        "triggers": {
                            "turn_start": [
                                {"type": "lose_hp", "amount": 1},
                                {"type": "block", "amount": block_amt},
                            ]
                        },
                    }
                )
                return effects
            if cid == "CRUELTY":
                percent = v(None, "CrueltyPower", upgraded)
                effects.append(
                    {"type": "gain_power", "power_id": "CRUELTY", "triggers": {"magnitude": percent}}
                )
                return effects
            if cid == "DARK_EMBRACE":
                effects.append(
                    {
                        "type": "gain_power",
                        "power_id": "DARK_EMBRACE",
                        "triggers": {"on_exhaust": [{"type": "draw", "amount": 1}]},
                    }
                )
                return effects
            if cid == "DEMON_FORM":
                amount = v(None, "StrengthPower", upgraded)
                effects.append(
                    {
                        "type": "gain_power",
                        "power_id": "DEMON_FORM",
                        "triggers": {"turn_start": [{"type": "gain_strength", "amount": amount}]},
                    }
                )
                return effects
            if cid == "FEEL_NO_PAIN":
                amount = v(None, "Power", upgraded)
                effects.append(
                    {
                        "type": "gain_power",
                        "power_id": "FEEL_NO_PAIN",
                        "triggers": {"on_exhaust": [{"type": "block", "amount": amount}]},
                    }
                )
                return effects
            if cid == "INFERNO":
                amount = v(None, "InfernoPower", upgraded)
                effects.append(
                    {
                        "type": "gain_power",
                        "power_id": "INFERNO",
                        "triggers": {
                            "turn_start": [{"type": "lose_hp", "amount": 1}],
                            "hp_lost_own_turn": [{"type": "damage_all", "amount": amount, "hits": 1}],
                        },
                    }
                )
                return effects
            if cid == "JUGGERNAUT":
                amount = v(None, "JuggernautPower", upgraded)
                effects.append(
                    {
                        "type": "gain_power",
                        "power_id": "JUGGERNAUT",
                        "triggers": {"block_gained": [{"type": "damage_random_enemy", "amount": amount}]},
                    }
                )
                return effects
            if cid == "PYRE":
                amount = v(None, "Energy", upgraded)
                effects.append(
                    {
                        "type": "gain_power",
                        "power_id": "PYRE",
                        "triggers": {"turn_start": [{"type": "gain_energy", "amount": amount}]},
                    }
                )
                return effects
            if cid == "RUPTURE":
                # Unlike Demon Form etc., Rupture's raw upgrade key is
                # "strength" (not "strengthpower"), which only matches the
                # "Strength" var in apply_upgrade_diffs()'s case-insensitive
                # lookup, not "StrengthPower" -- both are 1 at base (true
                # duplicates), but only "Strength" actually receives the
                # +1 upgrade diff, so read that one specifically for this
                # card to avoid silently keeping the un-upgraded amount.
                amount = v(None, "Strength", upgraded)
                effects.append(
                    {
                        "type": "gain_power",
                        "power_id": "RUPTURE",
                        "triggers": {"hp_lost_own_turn": [{"type": "gain_strength", "amount": amount}]},
                    }
                )
                return effects
            if cid == "TANK":
                effects.append({"type": "gain_power", "power_id": "TANK"})
                return effects
            if cid == "UNMOVABLE":
                effects.append({"type": "gain_power", "power_id": "UNMOVABLE"})
                return effects
            if cid == "VICIOUS":
                amount = v(None, "Cards", upgraded)
                effects.append(
                    {
                        "type": "gain_power",
                        "power_id": "VICIOUS",
                        "triggers": {"vulnerable_applied": [{"type": "draw", "amount": amount}]},
                    }
                )
                return effects

            # --- Stage 2: complex-mechanic cards (self-contained new effect types) ---
            if cid == "ASHEN_STRIKE":
                base = v("damage", "CalculationBase", upgraded)
                per_card = v(None, "ExtraDamage", upgraded)
                effects.append(
                    {"type": "damage_scales_with_pile_count", "pile": "exhaust_pile", "base": base, "per_card": per_card}
                )
                return effects
            if cid == "BATTLE_TRANCE":
                effects.append({"type": "draw", "amount": v("cards_draw", "Cards", upgraded)})
                effects.append({"type": "set_no_draw_this_turn"})
                return effects
            if cid == "DRUM_OF_BATTLE":
                effects.append({"type": "draw", "amount": v("cards_draw", "Cards", upgraded)})
                # on_exhaust_effects (the Energy gain) is attached to `entry`
                # separately below build_effects(), not part of this list.
                return effects
            if cid == "EVIL_EYE":
                block_amt = v("block", "Block", upgraded)
                effects.append({"type": "block", "amount": block_amt})
                effects.append(
                    {
                        "type": "conditional",
                        "if": "exhausted_this_turn",
                        "then": [{"type": "block", "amount": block_amt}],
                    }
                )
                return effects
            if cid == "FIEND_FIRE":
                effects.append({"type": "exhaust_hand_and_damage", "per_card": v("damage", "Damage", upgraded)})
                return effects
            if cid == "FORGOTTEN_RITUAL":
                energy_amt = v("energy_gain", "Energy", upgraded)
                effects.append(
                    {
                        "type": "conditional",
                        "if": "exhausted_this_turn",
                        "then": [{"type": "gain_energy", "amount": energy_amt}],
                    }
                )
                return effects
            if cid == "MANGLE":
                effects.append({"type": "damage", "amount": v("damage", "Damage", upgraded), "hits": 1})
                effects.append(
                    {"type": "enemy_lose_strength_this_turn", "amount": v(None, "StrengthLoss", upgraded)}
                )
                return effects
            if cid == "MOLTEN_FIST":
                effects.append({"type": "damage", "amount": v("damage", "Damage", upgraded), "hits": 1})
                effects.append({"type": "double_target_vulnerable"})
                return effects
            if cid == "PACTS_END":
                effects.append(
                    {
                        "type": "conditional",
                        "if": "exhaust_pile_size_gte_3",
                        "then": [{"type": "damage_all", "amount": v("damage", "Damage", upgraded), "hits": 1}],
                    }
                )
                return effects
            if cid == "PILLAGE":
                effects.append({"type": "damage", "amount": v("damage", "Damage", upgraded), "hits": 1})
                effects.append({"type": "draw_until_non_attack"})
                return effects
            if cid == "SECOND_WIND":
                effects.append(
                    {
                        "type": "exhaust_hand_filtered_and_block",
                        "filter": "non_attack",
                        "per_card": v("block", "Block", upgraded),
                    }
                )
                return effects
            if cid == "SPITE":
                dmg = v("damage", "Damage", upgraded)
                hits = v(None, "Repeat", upgraded)
                effects.append(
                    {
                        "type": "conditional",
                        "if": "lost_hp_this_turn",
                        "then": [{"type": "damage", "amount": dmg, "hits": hits}],
                        "else": [{"type": "damage", "amount": dmg, "hits": 1}],
                    }
                )
                return effects
            if cid == "THRASH":
                effects.append(
                    {"type": "exhaust_random_attack_and_add_damage", "base": v("damage", "Damage", upgraded), "hits": 2}
                )
                return effects

            dmg = v("damage", "Damage", upgraded)
            if dmg:
                hits = v("hit_count", "HitCount", upgraded) or c.get("hit_count") or 1
                if target == "single_enemy":
                    effects.append({"type": "damage", "amount": dmg, "hits": hits})
                else:
                    effects.append({"type": "damage_all", "amount": dmg, "hits": hits})

            block = v("block", "Block", upgraded)
            if block:
                effects.append({"type": "block", "amount": block})

            draw = v("cards_draw", "Cards", upgraded)
            if draw:
                effects.append({"type": "draw", "amount": draw})

            energy = v("energy_gain", "Energy", upgraded)
            if energy:
                effects.append({"type": "gain_energy", "amount": energy})

            hp_loss = v("hp_loss", "HpLoss", upgraded)
            if hp_loss:
                effects.append({"type": "lose_hp", "amount": hp_loss})

            # Strength: text says "this turn" -> temporary, else permanent.
            if "Strength" in mentioned_powers:
                amount = v(None, "StrengthPower", upgraded)
                if amount:
                    is_this_turn = "this turn" in desc
                    effects.append(
                        {"type": "gain_strength_this_turn" if is_this_turn else "gain_strength", "amount": amount}
                    )

            # Vulnerable / Weak: some cards (e.g. Uppercut) share a single
            # "Power" var for both -- in every Ironclad card observed, when
            # both are mentioned together they use the same amount, so this
            # is safe. Verified manually against Uppercut's known text.
            shared_power_amount = v(None, "Power", upgraded)
            if "Vulnerable" in mentioned_powers:
                amount = v(None, "VulnerablePower", upgraded) or shared_power_amount
                if amount:
                    kind = "apply_vulnerable_all" if target == "all_enemies" else "apply_vulnerable"
                    effects.append({"type": kind, "amount": amount})
            if "Weak" in mentioned_powers:
                amount = v(None, "WeakPower", upgraded) or shared_power_amount
                if amount:
                    effects.append({"type": "apply_weak", "amount": amount})

            if EXHAUST_RANDOM_FROM_HAND_RE.search(desc):
                effects.append({"type": "exhaust_random_from_hand"})

            return effects

        base_effects = build_effects(False)
        upg_effects = build_effects(True)

        if not base_effects:
            excluded.append(
                {"id": c["id"], "name": c["name"], "reasons": ["no derivable effects from structured fields (likely a bespoke mechanic)"]}
            )
            continue

        SPECIAL_CASED_IDS = {"BODY_SLAM", "PERFECTED_STRIKE", "DISMANTLE"} | SIMPLE_BESPOKE_CARDS
        if c["id"] not in SPECIAL_CASED_IDS:
            unconsumed = set(c.get("vars") or {}) - consumed_var_keys - IGNORE_VAR_KEYS
            if unconsumed:
                excluded.append(
                    {
                        "id": c["id"],
                        "name": c["name"],
                        "reasons": [
                            f"unconsumed numeric var(s) {sorted(unconsumed)} -- description likely has a "
                            "secondary mechanic (scaling/conditional/duration) not covered by the generic "
                            "damage/block/draw/energy/hp_loss/power extraction"
                        ],
                    }
                )
                continue

        # Most upgrade diffs are deltas ("+2"), but "cost" is given as an
        # absolute new value (e.g. Body Slam+ costs 0, not base_cost+0).
        upgrade_diffs = c.get("upgrade") or {}
        upgraded_cost = upgrade_diffs["cost"] if "cost" in upgrade_diffs else (c["cost"] or 0)

        entry = {
            "id": c["id"],
            "name": c["name"],
            "type": c["type"],
            "rarity": c["rarity"],
            "cost": c["cost"] or 0,
            "target": target,
            "effects": base_effects,
            "upgraded_cost": upgraded_cost,
            "effects_upgraded": upg_effects,
            "source_description": desc,
            "source_description_upgraded": c.get("upgrade_description") or desc,
        }
        if "Exhaust" in (c.get("keywords") or []):
            entry["self_exhausts"] = True
        if c["id"] == "DRUM_OF_BATTLE":
            # "When this card is Exhausted, gain Energy" -- attached here
            # rather than inside build_effects() since on_exhaust_effects is
            # a separate field from effects/effects_upgraded (see CardDef in
            # sim/card.py). Energy amount scales with upgrade (2 -> 3).
            entry["on_exhaust_effects"] = [{"type": "gain_energy", "amount": v("energy_gain", "Energy", False)}]
            entry["on_exhaust_effects_upgraded"] = [
                {"type": "gain_energy", "amount": v("energy_gain", "Energy", True)}
            ]
        out_cards.append(entry)

    return {"cards": out_cards, "excluded": excluded}


# Monsters we personally observed via real mcp__sts2__get_game_state gameplay
# during the 2026-07-09 Phase A verification session (see
# _hand_observed_monsters.json). That's first-hand
# ground truth from actually playing the game, strictly more trustworthy
# than an automated read of spire-codex's attack-pattern state machine --
# so these IDs are kept exactly as hand-curated and never overwritten by
# the codex conversion below, even where the codex data disagrees (e.g.
# FUZZY_WURM_CRAWLER's real FSM has an ambiguous branch we can't safely
# resolve automatically -- see the "cycle must be a single fully-explicit
# closed loop" requirement below, which that monster fails).
HAND_OBSERVED_MONSTER_IDS = {
    "NIBBIT",
    "SNAPPING_JAXFRUIT",
    "FUZZY_WURM_CRAWLER",
    "SHRINKER_BEETLE",
    "CUBEX_CONSTRUCT",
    "TRACKER_RUBY_RAIDER",
    "CROSSBOW_RUBY_RAIDER",
    "ASSASSIN_RUBY_RAIDER",
}

# spire-codex's real region labels, cross-checked against _codex_monsters_raw
# .json's encounters[].act field (see plan/plan.md's 2026-07-10 entry --
# there are 4 regions, not 3 "acts": Act 1 splits into two parallel zones).
REGIONS = ["Act 1 - Overgrowth", "Act 1 - Underdocks", "Act 2 - Hive", "Act 3 - Glory"]
REGION_FILE_STEMS = {
    "Act 1 - Overgrowth": "act1_overgrowth",
    "Act 1 - Underdocks": "act1_underdocks",
    "Act 2 - Hive": "act2_hive",
    "Act 3 - Glory": "act3_glory",
}
# Hand-observed monsters were kept verbatim (not re-derived from codex data)
# -- their stats and `region` field live in _hand_observed_monsters.json,
# each region assigned by cross-referencing which encounter it was actually
# fought in during the 2026-07-09 Phase A playthrough against spire-codex's
# encounters[].act field for that id.

# v1's approved status-effect set, applied to monster moves. Any move using
# a power outside this map (Thorns, Ritual, Constrict, Minion, etc. -- see
# the survey of _codex_powers_raw.json's power_id usage across Normal
# monsters) excludes the whole monster rather than silently dropping the
# power, matching the card-conversion policy above.
MONSTER_POWER_EFFECT_MAP = {
    ("STRENGTH", "self"): "buff_strength",
    ("WEAK", "player"): "debuff_player_weak",
    ("VULNERABLE", "player"): "debuff_player_vulnerable",
    ("FRAIL", "player"): "debuff_player_frail",
}


def _build_monster_cycle(monster: dict) -> list[dict] | None:
    """Walks a spire-codex attack_pattern state graph into a linear,
    repeating ai_cycle list (the schema sim/combat.py's EnemyState already
    consumes). Returns None if the pattern can't be safely reduced to a
    single deterministic closed loop -- e.g. it has real random/weighted
    branches, a state with no explicit `next` in the middle of the loop
    (ambiguous continuation, exactly the FUZZY_WURM_CRAWLER case), or any
    move along the way uses an unsupported power/heal/hit-count-less-amount
    combination. Conservative by design: every excluded monster is better
    than a wrong one for RL training on top of it.
    """
    pattern = monster.get("attack_pattern")
    if not pattern or pattern.get("type") != "cycle":
        return None

    moves_by_id = {mv["id"]: mv for mv in (monster.get("moves") or [])}
    states_by_id = {s["id"]: s for s in pattern["states"]}
    move_states = {sid: s for sid, s in states_by_id.items() if s["type"] == "move"}
    if not move_states or len(move_states) != len(states_by_id):
        return None  # any non-move state (conditional/branch) disqualifies "cycle" type here

    # `initial_move` inconsistently refers to either a state's own `id`
    # (e.g. Nibbit's "INIT_MOVE") or a state's `move_id` field (e.g.
    # Seapunk's "SEA_KICK", which names state "SEA_KICK_MOVE") depending on
    # the monster -- found by comparing Seapunk's initial_move against its
    # states after it was wrongly excluded by an id-only match. Try both.
    raw_start = pattern.get("initial_move")
    if raw_start in move_states:
        start_id = raw_start
    else:
        matches = [sid for sid, s in move_states.items() if s.get("move_id") == raw_start]
        start_id = matches[0] if len(matches) == 1 else None
    if start_id is None:
        return None

    # Follow explicit `next` pointers only -- a null `next` means we can't
    # prove where the pattern goes next without guessing, so bail rather
    # than assume array order (see FUZZY_WURM_CRAWLER investigation notes
    # in plan/plan.md's 2026-07-10 entry for why array-order was rejected
    # as an unverified assumption).
    visited_order: list[str] = []
    seen: set[str] = set()
    cur = start_id
    while cur not in seen:
        seen.add(cur)
        visited_order.append(cur)
        nxt = move_states[cur].get("next")
        if nxt is None or nxt not in move_states:
            return None
        cur = nxt
    if cur != start_id or len(visited_order) != len(move_states):
        return None  # loop closes somewhere other than start, or doesn't cover every state

    cycle: list[dict] = []
    for sid in visited_order:
        move_id = move_states[sid]["move_id"]
        mv = moves_by_id.get(move_id)
        if mv is None:
            return None
        if mv.get("heal"):
            return None  # heal not in v1's approved mechanic set
        turn_effect: dict = {}
        dmg = mv.get("damage")
        if dmg and dmg.get("normal"):
            turn_effect["attack"] = {"amount": dmg["normal"], "hits": dmg.get("hit_count") or 1}
        if mv.get("block"):
            turn_effect["block"] = mv["block"]
        for p in (mv.get("powers") or []):
            key = (p["power_id"], p.get("target"))
            if key not in MONSTER_POWER_EFFECT_MAP:
                return None
            eff_key = MONSTER_POWER_EFFECT_MAP[key]
            turn_effect[eff_key] = turn_effect.get(eff_key, 0) + p["amount"]
        if not turn_effect:
            return None  # move with no modeled effect at all (shouldn't happen, but don't guess)
        cycle.append(turn_effect)
    return cycle


def convert_monsters() -> dict:
    all_monsters = load("_codex_monsters_raw.json")
    out_monsters = {}
    excluded = []

    for m in all_monsters:
        if m["id"] in HAND_OBSERVED_MONSTER_IDS:
            continue  # kept as hand-curated ground truth, see _hand_observed_monsters.json
        if m["type"] != "Normal":
            excluded.append({"id": m["id"], "reason": f"{m['type']} tier (elites/bosses deferred, out of v1 scope)"})
            continue
        acts = {e.get("act") for e in (m.get("encounters") or [])}
        act1_regions = sorted(a for a in acts if a in ("Act 1 - Overgrowth", "Act 1 - Underdocks"))
        if not act1_regions:
            excluded.append({"id": m["id"], "reason": f"no Act 1 encounter (acts: {sorted(a for a in acts if a)})"})
            continue
        if m.get("innate_powers"):
            excluded.append({"id": m["id"], "reason": "has innate_powers (e.g. Artifact) not modeled in v1"})
            continue

        cycle = _build_monster_cycle(m)
        if cycle is None:
            excluded.append({"id": m["id"], "reason": "attack pattern isn't a single fully-explicit deterministic cycle, or uses an unsupported power/heal move"})
            continue

        out_monsters[m["id"]] = {
            "display_name": m["name"],
            "hp": m["min_hp"],
            "ai_cycle": cycle,
            "region": act1_regions[0],
            "source": "spire-codex attack_pattern/moves data (non-ascension values; min_hp used as the fixed HP)",
        }

    return {"monsters": out_monsters, "excluded": excluded}


def _cards_meta_for(color: str) -> dict:
    return {
        "source": (
            f"Converted from the spire-codex live API (https://spire-codex.com, "
            f"https://github.com/ptrlrd/spire-codex) via convert_from_spire_codex.py "
            f"on 2026-07-10, filtered to color=='{color}'. spire-codex's data is "
            "reverse-engineered from Slay the Spire 2's own decompiled game files "
            "(GDRE Tools PCK extraction + ILSpy decompilation of sts2.dll), not "
            "hand-transcribed from gameplay."
        ),
        "license": (
            "PolyForm Noncommercial License 1.0.0. Required Notice: "
            "Copyright (C) 2025-present Peter Lord and Spire Codex contributors. "
            "https://polyformproject.org/licenses/noncommercial/1.0.0 -- this "
            "data (and any model trained on it) is NONCOMMERCIAL USE ONLY."
        ),
        "scope_limitation": (
            "As of Stage 2 (2026-07-10), includes a data-driven triggered-Power "
            "system (turn-start / on-exhaust / on-HP-lost-this-turn / "
            "on-block-gained / on-Vulnerable-applied hooks) covering 14 Power-type "
            "cards and a generic `conditional` if/then/else wrapper covering "
            "'this turn' condition cards. Still excludes: recursive auto-play "
            "(Stampede/Havoc/Hellraiser/Howl from Beyond), dynamic card costs "
            "(Corruption/Stomp), card-instance-persistent damage scaling (Rampage), "
            "'next card played' pending modifiers (One Two Punch/Unrelenting/Rage), "
            "card-spawning effects, RandomEnemy/AnyAlly targeting, X-cost cards, "
            "and any card whose description implies a mechanic beyond the above "
            "(verified via both a complex-mechanic text-pattern denylist and a "
            "systematic unconsumed-numeric-var check -- see "
            "convert_from_spire_codex.py). See the 'excluded' list in "
            "_conversion_report_<color>.json for every excluded card and why."
        ),
        "effect_schema": _EFFECT_SCHEMA,
    }


NOT_YET_CONVERTED_CARDS_META = {
    "status": "not_yet_converted",
    "note": (
        "Placeholder -- this class hasn't been run through the spire-codex "
        "conversion + manual audit pipeline yet (Stage 3 of plan/plan.md's "
        "roadmap, 2026-07-10 entry). convert_cards(color=...) in "
        "convert_from_spire_codex.py works for any color already, but its "
        "mechanic denylist/unconsumed-var check were only tuned and spot-"
        "checked against Ironclad's card text -- running it as-is on another "
        "class would silently trust an unaudited result. Some classes also "
        "need bespoke subsystems this pipeline doesn't have yet (e.g. "
        "Defect's orbs)."
    ),
}

NOT_YET_CONVERTED_MONSTERS_META = {
    "status": "not_yet_converted",
    "note": (
        "Placeholder -- this region hasn't been converted yet (Stage 4 of "
        "plan/plan.md's roadmap, 2026-07-10 entry). convert_monsters() in "
        "convert_from_spire_codex.py currently only looks at Act 1 "
        "(Overgrowth/Underdocks) encounters."
    ),
}

_EFFECT_SCHEMA = {
        "damage": "Deal `amount` damage to the target, `hits` times (default 1).",
        "damage_all": "Deal `amount` damage to every enemy, `hits` times (default 1).",
        "damage_equals_block": "Deal damage to target equal to the player's current block.",
        "damage_double_if_target_vulnerable": "Deal `amount` damage; if target has Vulnerable, deal it again (two separate hits).",
        "damage_scales_with_tag": "Deal `base` + `per_tag` * (count of cards in the player's whole deck whose name contains `tag`) damage.",
        "block": "Gain `amount` block.",
        "draw": "Draw `amount` cards.",
        "gain_energy": "Gain `amount` energy this turn.",
        "lose_hp": "Lose `amount` HP directly (not blocked).",
        "apply_vulnerable": "Apply `amount` stacks of Vulnerable to the target.",
        "apply_vulnerable_all": "Apply `amount` stacks of Vulnerable to every enemy.",
        "apply_weak": "Apply `amount` stacks of Weak to the target.",
        "gain_strength": "Gain `amount` Strength permanently (for the rest of combat).",
        "gain_strength_this_turn": "Gain `amount` Strength for the remainder of the current turn only.",
        "exhaust_random_from_hand": "Exhaust one random other card from hand.",
        "upgrade_random_card_in_hand": "Upgrade one random not-yet-upgraded card in hand for the rest of combat.",
        "move_card_discard_to_draw_top": "Move one random card from the discard pile to the top of the draw pile.",
        # --- Stage 2 additions (2026-07-10) ---
        "gain_power": "Grant a persistent Power (`power_id`) for the rest of combat, with an optional `triggers` dict (event name -> effects list, or \"magnitude\" -> int for continuously-checked powers like Cruelty).",
        "conditional": "If `if` (a named this-turn/this-combat condition) is true, apply the `then` effects list, else the optional `else` list.",
        "damage_random_enemy": "Deal `amount` damage to one random living enemy.",
        "damage_scales_with_pile_count": "Deal `base` + `per_card` * (size of `pile`, e.g. \"exhaust_pile\") damage.",
        "enemy_lose_strength_this_turn": "Reduce the target enemy's effective Strength by `amount` until its next turn resolves.",
        "double_target_vulnerable": "Double the target's current Vulnerable stacks.",
        "set_no_draw_this_turn": "Block any further `draw`/`draw_until_non_attack` effects for the rest of this turn.",
        "return_random_attack_from_discard_upgraded": "Move one random Attack from the discard pile into hand, upgraded.",
        "exhaust_hand_and_damage": "Exhaust the rest of the hand, then deal `per_card` damage to target for each card exhausted this way.",
        "exhaust_hand_filtered_and_block": "Exhaust hand cards matching `filter` (currently only \"non_attack\"), then gain `per_card` block for each.",
        "exhaust_random_attack_and_add_damage": "Exhaust one random other Attack from hand and add its own base damage to this card's `base` damage, dealt `hits` times.",
        "draw_until_non_attack": "Draw one card at a time until a non-Attack card is drawn (or hand/piles are exhausted).",
}


def _monsters_meta_for(region: str) -> dict:
    return {
        "source": (
            f"Two-tier sourcing for {region}, documented per-monster: (1) "
            "hand-observed monsters (the 8 in _hand_observed_monsters.json) "
            "come from real mcp__sts2__get_game_state observations during the "
            "2026-07-09 Phase A playthrough -- first-hand gameplay data, kept "
            "verbatim. (2) All other monsters are converted from the "
            "spire-codex live API's attack_pattern/moves data via "
            "convert_from_spire_codex.py, restricted to monsters whose attack "
            "pattern reduces to a single fully-explicit deterministic repeating "
            "cycle (no random/weighted branches) using only v1's approved "
            "power set."
        ),
        "license": (
            "PolyForm Noncommercial License 1.0.0 applies to monsters sourced "
            "from spire-codex (tier 2 above, marked with a 'source' field on "
            "the monster entry itself). Required Notice: Copyright (C) "
            "2025-present Peter Lord and Spire Codex contributors. "
            "https://polyformproject.org/licenses/noncommercial/1.0.0 -- "
            "NONCOMMERCIAL USE ONLY. Hand-observed monsters (tier 1) carry no "
            "such restriction, being original first-hand gameplay observation."
        ),
        "limitation": (
            "Normal-tier only (elites/bosses excluded -- deferred to Stage 4 "
            "of plan/plan.md's roadmap). No ascension scaling (base/"
            "non-ascension values only). Monsters with innate_powers (e.g. "
            "Artifact), heal moves, or any power outside Strength/Vulnerable/"
            "Weak/Frail are excluded rather than modeled inaccurately -- see "
            "the 'excluded' list in _monster_conversion_report.json for every "
            "excluded monster and why."
        ),
    }


def write_outputs() -> None:
    cards_dir = DATA_DIR / "cards"
    monsters_dir = DATA_DIR / "monsters"
    cards_dir.mkdir(exist_ok=True)
    monsters_dir.mkdir(exist_ok=True)

    # --- cards, one file per class ---
    included_total = excluded_total = 0
    for color in ALL_CARD_COLORS:
        if color in AUDITED_CARD_COLORS:
            result = convert_cards(color)
            (DATA_DIR / f"_conversion_report_{color}.json").write_text(
                json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            out = {"_meta": _cards_meta_for(color), "cards": result["cards"]}
            included_total += len(result["cards"])
            excluded_total += len(result["excluded"])
        else:
            out = {"_meta": NOT_YET_CONVERTED_CARDS_META, "cards": []}
        (cards_dir / f"{color}.json").write_text(
            json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    print(f"cards: {included_total} included, {excluded_total} excluded (audited classes: {sorted(AUDITED_CARD_COLORS)})")

    # --- monsters, one file per region ---
    hand_observed = load("_hand_observed_monsters.json")
    monster_result = convert_monsters()
    (DATA_DIR / "_monster_conversion_report.json").write_text(
        json.dumps(monster_result, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    all_monsters = dict(hand_observed)
    all_monsters.update(monster_result["monsters"])

    region_counts = {}
    for region in REGIONS:
        stem = REGION_FILE_STEMS[region]
        region_monsters = {mid: m for mid, m in all_monsters.items() if m["region"] == region}
        if region_monsters:
            encounters = {f"{mid}_SOLO": {"enemies": [mid]} for mid in region_monsters}
            out = {"_meta": _monsters_meta_for(region), "monsters": region_monsters, "encounters": encounters}
        else:
            out = {"_meta": NOT_YET_CONVERTED_MONSTERS_META, "monsters": {}, "encounters": {}}
        (monsters_dir / f"{stem}.json").write_text(
            json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        region_counts[region] = len(region_monsters)

    print(f"monsters: {len(hand_observed)} hand-observed + {len(monster_result['monsters'])} codex-converted "
          f"= {len(all_monsters)} total, {len(monster_result['excluded'])} codex monsters excluded")
    for region, n in region_counts.items():
        print(f"  {region}: {n} monsters")
    print(f"wrote {cards_dir} and {monsters_dir}")


if __name__ == "__main__":
    write_outputs()
