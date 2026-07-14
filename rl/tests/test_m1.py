import torch

from sts2rl.checkpoint import load_checkpoint, save_checkpoint
from sts2rl.losses import generalized_advantage_estimate, behavior_cloning_loss
from sts2rl.model import CandidatePolicy, RecurrentCandidatePolicy
from sts2rl.observation import GLOBAL_FEATURE_DIM
from sts2rl.trajectory import Transition, TrajectoryWriter
from sts2rl.training import bc_update, ppo_update


def _batch(steps: int = 4, candidates: int = 3, candidate_dim: int = 5):
    generator = torch.Generator().manual_seed(0)
    return {
        "global": torch.randn(steps, GLOBAL_FEATURE_DIM, generator=generator),
        "candidates": torch.randn(steps, candidates, candidate_dim, generator=generator),
        "mask": torch.ones(steps, candidates, dtype=torch.bool),
        "targets": torch.randint(0, candidates, (steps,), generator=generator),
    }


def test_gae_and_bc_are_finite():
    adv, ret = generalized_advantage_estimate(torch.ones(3), torch.zeros(3), torch.zeros(3))
    assert torch.isfinite(adv).all() and torch.isfinite(ret).all()
    assert behavior_cloning_loss(torch.zeros(2, 3), torch.tensor([0, 1])).item() > 0


def test_candidate_policy_masks_invalid_candidates():
    model = CandidatePolicy()
    logits, value = model(torch.zeros(1, GLOBAL_FEATURE_DIM), torch.zeros(1, 3, 5), torch.tensor([[True, False, True]]))
    assert logits.shape == (1, 3) and value.shape == (1,)
    assert logits[0, 1] < -1e10


def test_recurrent_policy_resets_with_new_hidden_state():
    model = RecurrentCandidatePolicy()
    logits, values, hidden = model.forward_sequence(torch.zeros(1, 2, GLOBAL_FEATURE_DIM), torch.zeros(1, 2, 3, 5), torch.ones(1, 2, 3, dtype=torch.bool))
    assert logits.shape == (1, 2, 3) and values.shape == (1, 2) and hidden.shape[1] == 1


def test_optimizer_accepts_policy_parameters_before_any_forward():
    # Resume constructs the model from config and must be able to build the
    # optimizer without first replaying a batch through it.
    model = CandidatePolicy()
    torch.optim.Adam(model.parameters(), lr=3e-4)


def test_bc_update_can_overfit_a_single_batch():
    # Guards the whole gradient path: candidate encoder -> pointer -> masked cross
    # entropy. If masking or the pointer detached the graph, the loss would stall.
    batch = _batch()
    torch.manual_seed(0)
    model = CandidatePolicy()
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-2)
    losses = [bc_update(model, optimizer, batch["global"], batch["candidates"], batch["targets"], batch["mask"]) for _ in range(200)]
    assert losses[-1] < 0.05 * losses[0], f"training loop cannot memorize a fixed batch: {losses[0]:.3f} -> {losses[-1]:.3f}"


def test_checkpoint_round_trip_restores_model_optimizer_and_step(tmp_path):
    batch = _batch()
    torch.manual_seed(0)
    model = CandidatePolicy()
    optimizer = torch.optim.Adam(model.parameters(), lr=3e-4)
    bc_update(model, optimizer, batch["global"], batch["candidates"], batch["targets"], batch["mask"])
    save_checkpoint(tmp_path / "ck.pt", model, optimizer, step=7, config={"learning_rate": 3e-4}, seed_hash="abc")
    assert not list(tmp_path.glob("*.tmp")), "atomic checkpoint temp file leaked"

    torch.manual_seed(1)  # a fresh process would not share the training RNG state
    restored = CandidatePolicy()
    restored_optimizer = torch.optim.Adam(restored.parameters(), lr=3e-4)
    payload = load_checkpoint(tmp_path / "ck.pt", restored, restored_optimizer)

    assert payload["step"] == 7 and payload["seed_hash"] == "abc"
    for before, after in zip(model.state_dict().values(), restored.state_dict().values()):
        assert torch.equal(before, after)
    assert restored_optimizer.state_dict()["state"], "optimizer moment buffers were not restored"


def test_resumed_training_matches_uninterrupted_training(tmp_path):
    batch = _batch()
    ppo_batch = {
        "global": batch["global"], "candidates": batch["candidates"], "mask": batch["mask"],
        "actions": batch["targets"], "old_logp": torch.zeros(4),
        "advantages": torch.ones(4), "returns": torch.ones(4),
    }

    torch.manual_seed(0)
    reference = CandidatePolicy()
    reference_optimizer = torch.optim.Adam(reference.parameters(), lr=3e-4)
    bc_update(reference, reference_optimizer, batch["global"], batch["candidates"], batch["targets"], batch["mask"])
    save_checkpoint(tmp_path / "ck.pt", reference, reference_optimizer, step=1, config={})
    uninterrupted = ppo_update(reference, reference_optimizer, ppo_batch)

    torch.manual_seed(1)
    resumed = CandidatePolicy()
    resumed_optimizer = torch.optim.Adam(resumed.parameters(), lr=3e-4)
    load_checkpoint(tmp_path / "ck.pt", resumed, resumed_optimizer)
    after_resume = ppo_update(resumed, resumed_optimizer, ppo_batch)

    assert after_resume["loss"] == uninterrupted["loss"]
    for before, after in zip(reference.state_dict().values(), resumed.state_dict().values()):
        assert torch.equal(before, after)


def test_trajectory_writer_emits_parquet_and_jsonl(tmp_path):
    transition = Transition("seed-1", 0, {"decision": "event_choice"}, {"phase": "event_choice", "global": (1.0,)}, [{"action": "choose_option"}], {"action": "choose_option"}, 0.0, False, None, "0.2.0")
    TrajectoryWriter(tmp_path / "t.jsonl").write([transition])
    assert (tmp_path / "t.jsonl.jsonl").exists() or (tmp_path / "t.jsonl").exists()

    TrajectoryWriter(tmp_path / "t.parquet").write([transition])
    import pyarrow.parquet as pq
    assert pq.read_table(tmp_path / "t.parquet").num_rows == 1
