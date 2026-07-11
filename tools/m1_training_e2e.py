"""M1 acceptance for the training stack: real engine -> trajectories -> tensors ->
BC/PPO updates -> checkpoint -> crash -> resume in a fresh process.

The resume leg runs as a separate process on purpose. In-process "resume" only
proves that ``load_state_dict`` runs; it cannot show that a checkpoint is enough
to rebuild training after the process is gone, which is what M1 claims.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "rl" / "src"))

import torch

from sts2rl.checkpoint import load_checkpoint, save_checkpoint
from sts2rl.engine import EngineClient, RunConfig
from sts2rl.evaluator import collect_episode
from sts2rl.features import encode_batch
from sts2rl.model import CandidatePolicy
from sts2rl.protocol import ActionCandidate
from sts2rl.trajectory import TrajectoryWriter
from sts2rl.training import bc_update

CHARACTERS = ("Ironclad", "Silent", "Defect", "Necrobinder", "Regent")
ROOT = REPO_ROOT / "external" / "sts2-cli"
DLL = ROOT / "src" / "Sts2Headless" / "bin" / "Debug" / "net9.0" / "Sts2Headless.dll"
TOTAL_STEPS = 20
CHECKPOINT_STEP = 10
LEARNING_RATE = 3e-4


def candidate_from_command(command: dict) -> ActionCandidate:
    return ActionCandidate(command["action"], command.get("args", {}))


def parameter_hash(model: torch.nn.Module) -> str:
    digest = hashlib.sha256()
    for name, tensor in sorted(model.state_dict().items()):
        digest.update(name.encode())
        digest.update(repr(tensor.detach().cpu().flatten().tolist()).encode())
    return digest.hexdigest()


def collect(samples_path: Path, episodes_per_character: int) -> None:
    dotnet = os.environ.get("DOTNET_HOST_PATH") or shutil.which("dotnet")
    if not dotnet:
        raise SystemExit("dotnet not found (set DOTNET_HOST_PATH)")
    samples: list[dict] = []
    with EngineClient([dotnet, str(DLL)], cwd=ROOT, timeout=10, env={"STS2_GAME_DIR": os.environ["STS2_GAME_DIR"]}) as engine:
        for character in CHARACTERS:
            for index in range(episodes_per_character):
                seed = f"m1-train-{character}-{index}"
                result, transitions = collect_episode(engine, RunConfig(character, seed), seed=index)
                if result.error:
                    raise SystemExit(f"collection failed on {seed}: {result.error}")
                TrajectoryWriter(REPO_ROOT / "rl" / "runs" / "m1_trajectories" / f"{seed}.parquet").write(transitions)
                for transition in transitions:
                    # Only possible because the transition records the candidates the
                    # action was chosen from; this is the supervision target for BC.
                    chosen = transition.legal_actions.index(transition.action)
                    samples.append({"state": transition.state, "legal_actions": transition.legal_actions, "chosen": chosen})
    samples_path.parent.mkdir(parents=True, exist_ok=True)
    samples_path.write_text(json.dumps(samples) + "\n", encoding="utf-8")
    print(f"collected {len(samples)} decisions from {len(CHARACTERS) * episodes_per_character} real episodes")


def load_batches(samples_path: Path) -> list[dict[str, torch.Tensor]]:
    samples = json.loads(samples_path.read_text(encoding="utf-8"))
    rng = random.Random(0)
    rng.shuffle(samples)
    batches = []
    for start in range(0, min(len(samples), TOTAL_STEPS * 32), 32):
        chunk = samples[start:start + 32]
        if len(chunk) < 32:
            break
        batches.append(encode_batch([(s["state"], [candidate_from_command(c) for c in s["legal_actions"]], s["chosen"]) for s in chunk]))
    if len(batches) < TOTAL_STEPS:
        raise SystemExit(f"need {TOTAL_STEPS} batches, got {len(batches)}; collect more episodes")
    return batches


def train(samples_path: Path, *, start_step: int, stop_step: int, resume_from: Path | None, checkpoint_to: Path | None) -> dict:
    batches = load_batches(samples_path)
    torch.manual_seed(0)
    model = CandidatePolicy()
    optimizer = torch.optim.Adam(model.parameters(), lr=LEARNING_RATE)
    if resume_from is not None:
        payload = load_checkpoint(resume_from, model, optimizer)
        if payload["step"] != start_step:
            raise SystemExit(f"checkpoint is at step {payload['step']}, expected {start_step}")

    losses = []
    for step in range(start_step, stop_step):
        batch = batches[step]
        losses.append(bc_update(model, optimizer, batch["global"], batch["candidates"], batch["targets"], batch["mask"]))
        if checkpoint_to is not None and step + 1 == CHECKPOINT_STEP:
            save_checkpoint(checkpoint_to, model, optimizer, step=CHECKPOINT_STEP, config={"learning_rate": LEARNING_RATE}, seed_hash="m1")
    return {"losses": losses, "parameter_hash": parameter_hash(model)}


def learning_check(samples_path: Path, steps: int = 200) -> dict:
    """Random-policy data has nothing to clone -- its BC loss sits at the candidate
    entropy floor and does not fall. Overfitting one real batch is what shows the
    encoder/pointer/optimizer path can actually learn from engine features.
    """
    batch = load_batches(samples_path)[0]
    torch.manual_seed(0)
    model = CandidatePolicy()
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-2)
    losses = [bc_update(model, optimizer, batch["global"], batch["candidates"], batch["targets"], batch["mask"]) for _ in range(steps)]
    return {"first_loss": losses[0], "final_loss": losses[-1], "memorized": losses[-1] < 0.05 * losses[0]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("acceptance", "resume-leg"), default="acceptance")
    parser.add_argument("--samples", type=Path, default=REPO_ROOT / "rl" / "runs" / "m1_train_samples.json")
    parser.add_argument("--checkpoint", type=Path, default=REPO_ROOT / "rl" / "runs" / "m1_bc_step10.pt")
    parser.add_argument("--report", type=Path, default=REPO_ROOT / "rl" / "runs" / "m1_training_e2e.json")
    parser.add_argument("--episodes-per-character", type=int, default=4)
    args = parser.parse_args()

    if args.mode == "resume-leg":
        result = train(args.samples, start_step=CHECKPOINT_STEP, stop_step=TOTAL_STEPS, resume_from=args.checkpoint, checkpoint_to=None)
        print(json.dumps(result))
        return

    if not os.environ.get("STS2_GAME_DIR"):
        raise SystemExit("STS2_GAME_DIR required")
    if not DLL.exists():
        raise SystemExit(f"build first; missing {DLL}")

    collect(args.samples, args.episodes_per_character)

    uninterrupted = train(args.samples, start_step=0, stop_step=TOTAL_STEPS, resume_from=None, checkpoint_to=None)
    first_leg = train(args.samples, start_step=0, stop_step=CHECKPOINT_STEP, resume_from=None, checkpoint_to=args.checkpoint)

    # Fresh interpreter: nothing but the checkpoint file crosses the boundary.
    completed = subprocess.run(
        [sys.executable, __file__, "--mode", "resume-leg", "--samples", str(args.samples), "--checkpoint", str(args.checkpoint)],
        capture_output=True, text=True, check=True,
    )
    resumed = json.loads(completed.stdout.strip().splitlines()[-1])

    resumed_losses = first_leg["losses"] + resumed["losses"]
    losses_match = resumed_losses == uninterrupted["losses"]
    params_match = resumed["parameter_hash"] == uninterrupted["parameter_hash"]
    learning = learning_check(args.samples)
    report = {
        "total_steps": TOTAL_STEPS,
        "checkpoint_step": CHECKPOINT_STEP,
        "uninterrupted_parameter_hash": uninterrupted["parameter_hash"],
        "resumed_parameter_hash": resumed["parameter_hash"],
        "losses_match": losses_match,
        "parameters_match": params_match,
        "overfit_first_loss": learning["first_loss"],
        "overfit_final_loss": learning["final_loss"],
        "memorized_real_batch": learning["memorized"],
        "passed": losses_match and params_match and learning["memorized"],
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    if not report["passed"]:
        raise SystemExit("resume did not reproduce uninterrupted training")


if __name__ == "__main__":
    main()
