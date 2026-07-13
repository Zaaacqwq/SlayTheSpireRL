"""Route-planning probes: does the policy's map pick depend on the path ahead?

Each probe presents the SAME immediate map choice (two Monster nodes) under two
controlled full maps that differ only in what lies BEHIND the left node
(synergy vs control path), and reports the pick-probability shift for the left
node. The player state (low hp / high gold / ...) is what makes one path
better. A policy that cannot see the full map scores exactly 0 on every probe
(--strip-map proves it: both states become identical); map-aware training
should move these numbers before win rates do.
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

LEFT, RIGHT = "LEFT", "RIGHT"


@dataclass(frozen=True)
class Probe:
    name: str
    rationale: str
    player: dict
    synergy_path: tuple[str, str]  # rooms behind the left node
    control_path: tuple[str, str]
    expected_sign: int = 1  # +1: the synergy path should raise P(left)


LOW_HP = {"hp": 18, "max_hp": 80, "gold": 90, "deck_size": 12}
HEALTHY = {"hp": 76, "max_hp": 80, "gold": 90, "deck_size": 12}
RICH = {"hp": 60, "max_hp": 80, "gold": 320, "deck_size": 12}

PROBES: tuple[Probe, ...] = (
    Probe(
        "low_hp_wants_rest_path",
        "at 18/80 hp a path holding two rest sites is worth detouring for",
        player=LOW_HP,
        synergy_path=("RestSite", "RestSite"),
        control_path=("Monster", "Monster"),
    ),
    Probe(
        "rich_wants_shop_path",
        "with 320 gold a path holding two shops converts gold into power",
        player=RICH,
        synergy_path=("Shop", "Shop"),
        control_path=("Monster", "Monster"),
    ),
    Probe(
        "low_hp_avoids_elite_path",
        "at 18/80 hp a path forced through two elites is likely lethal",
        player=LOW_HP,
        synergy_path=("Elite", "Elite"),
        control_path=("Monster", "Monster"),
        expected_sign=-1,
    ),
    Probe(
        "healthy_hunts_elite_path",
        "at 76/80 hp elite relics are the best power spike available",
        player=HEALTHY,
        synergy_path=("Elite", "Monster"),
        control_path=("Monster", "Monster"),
    ),
)


def _node(col, row, node_type, children=(), visited=False, current=False):
    return {
        "col": col, "row": row, "type": node_type,
        "children": [{"col": c, "row": r} for c, r in children],
        "visited": visited, "current": current,
    }


def map_select_state(player: dict, left_path: tuple[str, str],
                     strip_map: bool = False) -> dict:
    state = {
        "type": "decision",
        "decision": "map_select",
        "context": {"act": 1, "floor": 2},
        "choices": [
            {"col": 1, "row": 1, "type": "Monster"},
            {"col": 3, "row": 1, "type": "Monster"},
        ],
        "player": {"name": "The Ironclad", "block": 0, **player},
    }
    if strip_map:
        return state
    state["map"] = {
        "rows": [
            [_node(2, 0, "Monster", children=[(1, 1), (3, 1)], visited=True, current=True)],
            [_node(1, 1, "Monster", children=[(1, 2)]),
             _node(3, 1, "Monster", children=[(3, 2)])],
            [_node(1, 2, left_path[0], children=[(1, 3)]),
             _node(3, 2, "Monster", children=[(3, 3)])],
            [_node(1, 3, left_path[1], children=[(2, 4)]),
             _node(3, 3, "Monster", children=[(2, 4)])],
        ],
        "boss": {"col": 2, "row": 4, "type": "Boss"},
        "current_coord": {"col": 2, "row": 0},
    }
    return state


def pick_probabilities(model, vocab: EntityVocab, state: dict) -> dict[str, float]:
    candidates = [
        ActionCandidate("select_map_node", {"col": 1, "row": 1}),
        ActionCandidate("select_map_node", {"col": 3, "row": 1}),
    ]
    observation = normalize_state(state)
    entities = encode_entity_batch([observation], vocab)
    slots = torch.tensor([candidate_entity_slots(observation, candidates)])
    with torch.no_grad():
        logits, _, _ = model(
            encode_global(state).unsqueeze(0), entities,
            encode_candidates(candidates).unsqueeze(0), candidate_slots=slots,
        )
    probabilities = torch.softmax(logits[0], -1).tolist()
    return dict(zip((LEFT, RIGHT), probabilities))


def run_probes(model, vocab: EntityVocab, strip_map: bool = False) -> list[dict]:
    results = []
    for probe in PROBES:
        with_synergy = pick_probabilities(
            model, vocab, map_select_state(probe.player, probe.synergy_path, strip_map))
        with_control = pick_probabilities(
            model, vocab, map_select_state(probe.player, probe.control_path, strip_map))
        delta = with_synergy[LEFT] - with_control[LEFT]
        results.append({
            "probe": probe.name,
            "rationale": probe.rationale,
            "p_left_synergy": round(with_synergy[LEFT], 4),
            "p_left_control": round(with_control[LEFT], 4),
            "delta": round(delta, 4),
            "aligned": bool(delta * probe.expected_sign > 0),
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
    parser.add_argument("--strip-map", action="store_true",
                        help="hide the map: true map-blind reference, deltas are exactly 0")
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
        rows = run_probes(model, vocab, strip_map=args.strip_map)
        report.append({
            "checkpoint": ckpt_path.name,
            "iteration": int(payload["step"]),
            "migrated": bool(payload.get("migrated_keys")),
            "probes": rows,
        })
        for row in rows:
            print(json.dumps({"checkpoint": ckpt_path.name, "probe": row["probe"],
                              "delta": row["delta"], "aligned": row["aligned"],
                              "p_synergy": row["p_left_synergy"],
                              "p_control": row["p_left_control"]}))
    if args.output:
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"report written to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
