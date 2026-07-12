"""Harvest mid-run player loadouts for the boss-bridge combat stage.

Plays greedy episodes with a trained checkpoint on train-split seeds and
records the player's snapshot (deck/relics/potions/hp) at the deepest
map_select decision reached, i.e. the loadout the policy actually brings to
late Act 1. ``start_combat`` recreates cards from canonical ids, so upgrade
state is lost — an accepted approximation, recorded in the plan.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys

import torch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "rl" / "src"))
from sts2rl.agent import PolicyAgent
from sts2rl.checkpoint import load_checkpoint
from sts2rl.engine import EngineClient, RunConfig
from sts2rl.entities import EntityVocab
from sts2rl.features import CANDIDATE_FEATURE_DIM
from sts2rl.model import EntityRecurrentPolicy

CLI_ROOT = REPO_ROOT / "external" / "sts2-cli"
DLL = CLI_ROOT / "src" / "Sts2Headless" / "bin" / "Debug" / "net9.0" / "Sts2Headless.dll"
VOCAB_PATH = REPO_ROOT / "rl" / "schema" / "m2_vocab.json"
OUTPUT = REPO_ROOT / "rl" / "schema" / "m2_boss_loadouts.json"


def strip(prefix: str, value: str) -> str:
    return value[len(prefix):] if value.startswith(prefix) else value


def snapshot(state) -> dict | None:
    player = state.raw.get("player") or {}
    deck = [strip("CARD.", str(card.get("id"))) for card in player.get("deck") or [] if card.get("id")]
    if not deck:
        return None
    return {
        "hp": int(player.get("hp", 0)),
        "max_hp": int(player.get("max_hp", 80)),
        "deck": deck,
        "relics": [strip("RELIC.", str(r.get("id"))) for r in player.get("relics") or [] if r.get("id")],
        "potions": [strip("POTION.", str(p.get("id"))) for p in player.get("potions") or [] if p.get("id")],
        "floor": (state.raw.get("context") or {}).get("floor", 0),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkpoint", type=Path)
    parser.add_argument("--episodes", type=int, default=64)
    parser.add_argument("--min-floor", type=int, default=8)
    parser.add_argument("--hidden", type=int, default=128)
    parser.add_argument("--layers", type=int, default=2)
    parser.add_argument("--heads", type=int, default=4)
    args = parser.parse_args()

    vocab = EntityVocab.load(VOCAB_PATH)
    model = EntityRecurrentPolicy(vocab_size=vocab.size, candidate_dim=CANDIDATE_FEATURE_DIM,
                                  hidden=args.hidden, heads=args.heads, layers=args.layers)
    load_checkpoint(args.checkpoint, model)
    model.eval()
    agent = PolicyAgent(model, vocab)

    dotnet = os.environ.get("DOTNET_HOST_PATH") or shutil.which("dotnet") or "dotnet"
    loadouts: list[dict] = []
    with EngineClient([dotnet, str(DLL)], cwd=CLI_ROOT, timeout=15,
                      env={"STS2_GAME_DIR": os.environ["STS2_GAME_DIR"]}) as engine:
        for index in range(args.episodes):
            seed = f"m2-a0-ironclad-loadout-{index}"
            try:
                state = engine.reset(RunConfig("Ironclad", seed))
                hidden = None
                best: dict | None = None
                for _ in range(400):
                    if state.phase == "game_over":
                        break
                    context = state.raw.get("context") or {}
                    if int(context.get("act", 0) or 0) > 1:
                        break
                    if state.phase == "map_select":
                        candidate = snapshot(state)
                        if candidate and (best is None or candidate["floor"] > best["floor"]):
                            best = candidate
                    step = agent.act(state.raw, state.candidates, hidden, greedy=True)
                    hidden = step.hidden
                    state = engine.step(state.candidates[step.index]).state
                if best and best["floor"] >= args.min_floor:
                    loadouts.append(best)
            except Exception as exc:
                print(f"[warn] seed {seed}: {type(exc).__name__}", file=sys.stderr)

    if len(loadouts) < 8:
        raise SystemExit(f"only {len(loadouts)} loadouts harvested; need >= 8")
    payload = {
        "version": 1,
        "checkpoint": str(args.checkpoint),
        "min_floor": args.min_floor,
        "loadouts": loadouts,
    }
    OUTPUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    floors = sorted(l["floor"] for l in loadouts)
    print(json.dumps({"path": str(OUTPUT), "count": len(loadouts),
                      "floor_min": floors[0], "floor_median": floors[len(floors) // 2],
                      "floor_max": floors[-1],
                      "deck_sizes": sorted({len(l['deck']) for l in loadouts})}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
