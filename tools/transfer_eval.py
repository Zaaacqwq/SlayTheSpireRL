"""Evaluate one checkpoint on a chosen backend — the sim->real transfer probe.

The whole sim route lives or dies on transfer: a policy trained on the fast
simulator must still improve on the real engine. Run this with ``--backend sim``
(this box) to get the sim-side dev number, and ``--backend real`` on a machine
with the game to get the real-side number. A large sim>>real gap means sim wins
do not carry over; a matching gap means the sim route is a valid accelerator.

Greedy, no gradient updates — pure evaluation on the held-out dev seeds.

Usage:
  python tools/transfer_eval.py --run-dir rl/runs/p2_cap_h256 --backend sim --episodes 50
  python tools/transfer_eval.py --run-dir rl/runs/p2_cap_h256 --backend real --episodes 50
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "rl" / "src"))

import torch  # noqa: E402

from sts2rl.agent import PolicyAgent  # noqa: E402
from sts2rl.checkpoint import load_checkpoint  # noqa: E402
from sts2rl.curriculum import CurriculumStage  # noqa: E402
from sts2rl.engine import EngineClient  # noqa: E402
from sts2rl.entities import EntityVocab  # noqa: E402
from sts2rl.features import CANDIDATE_FEATURE_DIM  # noqa: E402
from sts2rl.model import EntityRecurrentPolicy  # noqa: E402
from sts2rl.ppo import PPOConfig, run_episode  # noqa: E402

CLI_ROOT = REPO_ROOT / "external" / "sts2-cli"
DLL = CLI_ROOT / "src" / "Sts2Headless" / "bin" / "Debug" / "net9.0" / "Sts2Headless.dll"
SPLIT_PATH = REPO_ROOT / "rl" / "seeds" / "m2_ironclad_seed_split.json"


def _make_backend(kind: str):
    if kind in ("sim", "zhiyue"):
        from sts2rl.sim import ZhiyueRunBackend
        return ZhiyueRunBackend()
    dotnet = os.environ.get("DOTNET_HOST_PATH") or shutil.which("dotnet") or "dotnet"
    return EngineClient(
        [dotnet, str(DLL)], cwd=CLI_ROOT,
        timeout=float(os.environ.get("STS2_TRAIN_TIMEOUT", "10")),
        env={"STS2_GAME_DIR": os.environ["STS2_GAME_DIR"]},
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True,
                        help="training run dir with config.json + entity_vocab.json")
    parser.add_argument("--checkpoint", type=Path, default=None,
                        help="checkpoint .pt (default: <run-dir>/resume.pt)")
    parser.add_argument("--backend", choices=("sim", "zhiyue", "real"), default="sim")
    parser.add_argument("--episodes", type=int, default=50)
    parser.add_argument("--max-act", type=int, default=1, help="stage clear act (1 = act1)")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--greedy", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()

    config = json.loads((args.run_dir / "config.json").read_text(encoding="utf-8"))
    vocab_path = args.run_dir / "entity_vocab.json"
    vocab = EntityVocab.load(vocab_path)
    checkpoint = args.checkpoint or (args.run_dir / "resume.pt")

    device = torch.device(args.device)
    model = EntityRecurrentPolicy(
        vocab_size=vocab.size, candidate_dim=CANDIDATE_FEATURE_DIM,
        hidden=int(config["hidden"]), heads=int(config["heads"]), layers=int(config["layers"]),
    ).to(device)
    payload = load_checkpoint(checkpoint, model)
    model.eval()
    agent = PolicyAgent(model, vocab)

    dev_seeds = list(json.loads(SPLIT_PATH.read_text(encoding="utf-8"))["development_seeds"])
    seeds = dev_seeds[: args.episodes]
    stage = CurriculumStage("act1" if args.max_act == 1 else f"acts_{args.max_act}",
                            max_act=args.max_act)
    cfg = PPOConfig()

    cleared = 0
    floors: list[float] = []
    acts: list[int] = []
    errors = 0
    backend = _make_backend(args.backend)
    try:
        for seed in seeds:
            record = run_episode(backend, stage, seed, agent, cfg, greedy=args.greedy)
            if record.error:
                errors += 1
                continue
            if record.outcome is True:
                cleared += 1
            floors.append(record.final_floor)
            acts.append(record.final_act)
    finally:
        backend.close()

    n = max(len(floors), 1)
    result = {
        "backend": args.backend,
        "checkpoint": str(checkpoint),
        "trained_step": int(payload.get("step", -1)),
        "vocab_size": vocab.size,
        "episodes": len(seeds),
        "errors": errors,
        "cleared_rate": round(cleared / max(len(seeds), 1), 4),
        "avg_cumulative_floor": round(sum(floors) / n, 2),
        "max_cumulative_floor": max(floors) if floors else 0.0,
        "reached_act2_rate": round(sum(a >= 2 for a in acts) / max(len(seeds), 1), 4),
    }
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
