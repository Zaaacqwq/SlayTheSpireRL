"""Deck-building synergy probes: does the policy's card pick depend on its deck?

Each probe presents the SAME card-reward offer under two controlled decks
(synergy vs control) and reports the pick-probability shift for a target card.
A policy that cannot see its deck scores ~0 on every probe; deck-aware
training should move these numbers before win rates do.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import sys

import torch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "rl" / "src"))
from sts2rl.checkpoint import load_checkpoint
from sts2rl.entities import EntityVocab, candidate_entity_slots, encode_entity_batch
from sts2rl.features import CANDIDATE_FEATURE_DIM, encode_candidates, encode_global
from sts2rl.model import EntityRecurrentPolicy
from sts2rl.observation import normalize_state
from sts2rl.protocol import ActionCandidate

VOCAB_PATH = REPO_ROOT / "rl" / "schema" / "m2_vocab.json"

# Feature-level card facts (cost/type/damage); the engine is not consulted.
CARDS: dict[str, dict] = {
    "STRIKE": {"id": "CARD.STRIKE_IRONCLAD", "name": "Strike", "cost": 1, "type": "Attack", "stats": {"damage": 6}},
    "DEFEND": {"id": "CARD.DEFEND_IRONCLAD", "name": "Defend", "cost": 1, "type": "Skill", "stats": {"block": 5}},
    "BASH": {"id": "CARD.BASH", "name": "Bash", "cost": 2, "type": "Attack", "stats": {"damage": 8}},
    "PERFECTED_STRIKE": {"id": "CARD.PERFECTED_STRIKE", "name": "Perfected Strike", "cost": 2, "type": "Attack", "stats": {"damage": 6}},
    "POMMEL_STRIKE": {"id": "CARD.POMMEL_STRIKE", "name": "Pommel Strike", "cost": 1, "type": "Attack", "stats": {"damage": 9}},
    "BLOODLETTING": {"id": "CARD.BLOODLETTING", "name": "Bloodletting", "cost": 0, "type": "Skill", "stats": {}},
    "HELLRAISER": {"id": "CARD.HELLRAISER", "name": "Hellraiser", "cost": 3, "type": "Attack", "stats": {"damage": 18}},
    "IRON_WAVE": {"id": "CARD.IRON_WAVE", "name": "Iron Wave", "cost": 1, "type": "Attack", "stats": {"damage": 5, "block": 5}},
    "SHRUG_IT_OFF": {"id": "CARD.SHRUG_IT_OFF", "name": "Shrug It Off", "cost": 1, "type": "Skill", "stats": {"block": 8}},
    "WHIRLWIND": {"id": "CARD.WHIRLWIND", "name": "Whirlwind", "cost": 4, "type": "Attack", "stats": {"damage": 5}},
    "BATTLE_TRANCE": {"id": "CARD.BATTLE_TRANCE", "name": "Battle Trance", "cost": 0, "type": "Skill", "stats": {}},
    "OFFERING": {"id": "CARD.OFFERING", "name": "Offering", "cost": 0, "type": "Skill", "stats": {}},
}

BASIC_DECK = ["STRIKE"] * 5 + ["DEFEND"] * 4 + ["BASH"]


@dataclass(frozen=True)
class Probe:
    name: str
    rationale: str
    offer: tuple[str, str, str]
    target: str
    synergy_deck: tuple[str, ...]
    control_deck: tuple[str, ...]
    expected_sign: int = 1  # +1: synergy deck should raise P(target)


PROBES: tuple[Probe, ...] = (
    Probe(
        "perfected_strike_wants_strikes",
        "Perfected Strike scales with cards named Strike",
        offer=("PERFECTED_STRIKE", "IRON_WAVE", "SHRUG_IT_OFF"),
        target="PERFECTED_STRIKE",
        synergy_deck=tuple(["STRIKE"] * 6 + ["POMMEL_STRIKE"] * 2 + ["DEFEND"] * 4),
        control_deck=tuple(["IRON_WAVE"] * 6 + ["BASH"] * 2 + ["DEFEND"] * 4),
    ),
    Probe(
        "pommel_strike_feeds_hellraiser",
        "cheap draw gets better when the deck holds a payoff card",
        offer=("POMMEL_STRIKE", "IRON_WAVE", "SHRUG_IT_OFF"),
        target="POMMEL_STRIKE",
        synergy_deck=tuple(["HELLRAISER"] + BASIC_DECK),
        control_deck=tuple(["BASH"] + BASIC_DECK),
    ),
    Probe(
        "bloodletting_fuels_expensive_deck",
        "energy gain matters more when the deck is expensive",
        offer=("BLOODLETTING", "IRON_WAVE", "SHRUG_IT_OFF"),
        target="BLOODLETTING",
        synergy_deck=tuple(["WHIRLWIND"] * 3 + ["HELLRAISER"] * 2 + ["BASH"] * 3 + ["DEFEND"] * 4),
        control_deck=tuple(["STRIKE"] * 4 + ["BLOODLETTING"] * 2 + ["DEFEND"] * 4 + ["BATTLE_TRANCE"] * 2),
    ),
    Probe(
        "skip_when_deck_is_bloated",
        "a lean deck draws its good cards more often",
        offer=("IRON_WAVE", "SHRUG_IT_OFF", "BATTLE_TRANCE"),
        target="__SKIP__",
        synergy_deck=tuple(BASIC_DECK * 3),  # 30 cards: skipping keeps it from getting worse
        control_deck=tuple(BASIC_DECK),      # 10 cards: adding a card is cheap
    ),
)


def card(entry: str, index: int | None = None) -> dict:
    payload = dict(CARDS[entry])
    payload["upgraded"] = False
    if index is not None:
        payload["index"] = index
    return payload


def card_reward_state(offer: tuple[str, str, str], deck: tuple[str, ...],
                      strip_deck: bool = False) -> dict:
    deck_cards = [] if strip_deck else [card(entry) for entry in deck]
    return {
        "type": "decision",
        "decision": "card_reward",
        "context": {"act": 1, "floor": 6, "room_type": "Monster"},
        "cards": [card(entry, index) for index, entry in enumerate(offer)],
        "can_skip": True,
        "relics": [],
        "player": {
            "name": "The Ironclad", "hp": 55, "max_hp": 80, "block": 0, "gold": 120,
            "deck_size": len(deck), "deck": deck_cards,
            "relics": [{"id": "RELIC.BURNING_BLOOD", "name": "Burning Blood"}],
            "potions": [],
        },
    }


def pick_probabilities(model, vocab: EntityVocab, state: dict) -> dict[str, float]:
    candidates = [
        ActionCandidate("select_card_reward", {"card_index": index})
        for index in range(len(state["cards"]))
    ] + [ActionCandidate("skip_card_reward", {})]
    observation = normalize_state(state)
    entities = encode_entity_batch([observation], vocab)
    slots = torch.tensor([candidate_entity_slots(observation, candidates)])
    with torch.no_grad():
        logits, _, _ = model(
            encode_global(state).unsqueeze(0), entities,
            encode_candidates(candidates).unsqueeze(0), candidate_slots=slots,
        )
    probabilities = torch.softmax(logits[0], -1).tolist()
    labels = [state["cards"][index]["id"] for index in range(len(state["cards"]))] + ["__SKIP__"]
    return dict(zip(labels, probabilities))


def run_probes(model, vocab: EntityVocab, strip_deck: bool = False) -> list[dict]:
    results = []
    for probe in PROBES:
        target = "__SKIP__" if probe.target == "__SKIP__" else CARDS[probe.target]["id"]
        with_synergy = pick_probabilities(
            model, vocab, card_reward_state(probe.offer, probe.synergy_deck, strip_deck))
        with_control = pick_probabilities(
            model, vocab, card_reward_state(probe.offer, probe.control_deck, strip_deck))
        delta = with_synergy[target] - with_control[target]
        results.append({
            "probe": probe.name,
            "rationale": probe.rationale,
            "target": target,
            "p_target_synergy": round(with_synergy[target], 4),
            "p_target_control": round(with_control[target], 4),
            "delta": round(delta, 4),
            "aligned": bool(delta * probe.expected_sign > 0),
            "distribution_synergy": {k: round(v, 4) for k, v in with_synergy.items()},
            "distribution_control": {k: round(v, 4) for k, v in with_control.items()},
        })
    return results


def select_checkpoints(run_dir: Path, spec: str) -> list[Path]:
    ckpts = sorted(run_dir.glob("ckpt_*.pt"))
    if not ckpts:
        raise SystemExit(f"no checkpoints under {run_dir}")
    if spec == "milestones":
        picks = sorted({0, len(ckpts) // 2, len(ckpts) - 1})
        return [ckpts[index] for index in picks]
    if spec == "latest":
        return [ckpts[-1]]
    if spec == "all":
        return ckpts
    by_step = {int(path.stem.split("_")[1]): path for path in ckpts}
    return [by_step[int(token)] for token in spec.split(",")]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-name", required=True)
    parser.add_argument("--runs-root", type=Path, default=REPO_ROOT / "rl" / "runs")
    parser.add_argument("--checkpoints", default="latest",
                        help="latest | milestones | all | comma-separated steps")
    parser.add_argument("--output", type=Path, default=None, help="write JSON here as well")
    parser.add_argument("--strip-deck", action="store_true",
                        help="hide the deck (deck_size only): true deck-blind reference, deltas must be ~0")
    args = parser.parse_args()

    run_dir = args.runs_root / args.run_name
    run_config = json.loads((run_dir / "config.json").read_text(encoding="utf-8"))
    vocab = EntityVocab.load(VOCAB_PATH)
    model = EntityRecurrentPolicy(
        vocab_size=vocab.size, candidate_dim=CANDIDATE_FEATURE_DIM,
        hidden=int(run_config.get("hidden", 128)),
        heads=int(run_config.get("heads", 4)),
        layers=int(run_config.get("layers", 2)),
    )

    report = []
    for ckpt_path in select_checkpoints(run_dir, args.checkpoints):
        payload = load_checkpoint(ckpt_path, model)
        model.eval()
        rows = run_probes(model, vocab, strip_deck=args.strip_deck)
        report.append({
            "checkpoint": ckpt_path.name,
            "iteration": int(payload["step"]),
            "migrated": bool(payload.get("migrated_keys")),
            "probes": rows,
        })
        for row in rows:
            print(json.dumps({"checkpoint": ckpt_path.name, "probe": row["probe"],
                              "delta": row["delta"], "aligned": row["aligned"],
                              "p_synergy": row["p_target_synergy"],
                              "p_control": row["p_target_control"]}))
    if args.output:
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"report written to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
