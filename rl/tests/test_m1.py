import torch
from sts2rl.losses import generalized_advantage_estimate, behavior_cloning_loss
from sts2rl.model import CandidatePolicy, RecurrentCandidatePolicy


def test_gae_and_bc_are_finite():
    adv, ret = generalized_advantage_estimate(torch.ones(3), torch.zeros(3), torch.zeros(3))
    assert torch.isfinite(adv).all() and torch.isfinite(ret).all()
    assert behavior_cloning_loss(torch.zeros(2, 3), torch.tensor([0, 1])).item() > 0


def test_candidate_policy_masks_invalid_candidates():
    model = CandidatePolicy()
    logits, value = model(torch.zeros(1, 8), torch.zeros(1, 3, 5), torch.tensor([[True, False, True]]))
    assert logits.shape == (1, 3) and value.shape == (1,)
    assert logits[0, 1] < -1e10


def test_recurrent_policy_resets_with_new_hidden_state():
    model = RecurrentCandidatePolicy()
    logits, values, hidden = model.forward_sequence(torch.zeros(1, 2, 8), torch.zeros(1, 2, 3, 5), torch.ones(1, 2, 3, dtype=torch.bool))
    assert logits.shape == (1, 2, 3) and values.shape == (1, 2) and hidden.shape[1] == 1
