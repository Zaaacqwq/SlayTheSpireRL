from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

torch = pytest.importorskip("torch")

from sts2rl.agent import PolicyAgent
from sts2rl.batch_inference import BatchedAgent
from sts2rl.entities import EntityVocab
from sts2rl.model import EntityRecurrentPolicy
from sts2rl.protocol import ActionCandidate

STATES = [
    {"decision": "combat_play", "player": {"hp": hp, "max_hp": 80},
     "hand": [{"index": 0, "id": "CARD.X", "cost": 1, "can_play": True}],
     "enemies": [{"index": 0, "name": "E", "hp": 20, "max_hp": 20}]}
    for hp in (10, 30, 50, 70)
]
CANDIDATES = [
    (ActionCandidate("play_card", {"card_index": 0}), ActionCandidate("end_turn", {})),
    (ActionCandidate("play_card", {"card_index": 0}),
     ActionCandidate("use_potion", {"potion_index": 0}), ActionCandidate("end_turn", {})),
    (ActionCandidate("end_turn", {}),),
    (ActionCandidate("play_card", {"card_index": 0}), ActionCandidate("end_turn", {})),
]


def make_model():
    torch.manual_seed(0)
    return EntityRecurrentPolicy(vocab_size=8, hidden=32, heads=2, layers=1)


def test_batched_agent_matches_single_agent_greedy():
    model = make_model()
    vocab = EntityVocab({})
    single = PolicyAgent(model, vocab)
    batched = BatchedAgent(model, vocab)
    try:
        for state, candidates in zip(STATES, CANDIDATES):
            a = single.act(state, candidates, greedy=True)
            b = batched.act(state, candidates, greedy=True)
            assert a.index == b.index
            assert a.logp == pytest.approx(b.logp, abs=1e-5)
            assert a.value == pytest.approx(b.value, abs=1e-5)
            assert a.hidden == pytest.approx(b.hidden, abs=1e-5)
    finally:
        batched.close()


def test_batched_agent_handles_concurrent_heterogeneous_requests():
    model = make_model()
    vocab = EntityVocab({})
    batched = BatchedAgent(model, vocab, wait_ms=5)
    single = PolicyAgent(model, vocab)
    try:
        def call(i):
            return batched.act(STATES[i], CANDIDATES[i], greedy=True)

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(call, range(4)))
        for i, step in enumerate(results):
            assert 0 <= step.index < len(CANDIDATES[i])
            expected = single.act(STATES[i], CANDIDATES[i], greedy=True)
            assert step.index == expected.index
            assert step.logp == pytest.approx(expected.logp, abs=1e-5)
    finally:
        batched.close()


def test_batched_agent_carries_hidden_state():
    model = make_model()
    vocab = EntityVocab({})
    batched = BatchedAgent(model, vocab)
    single = PolicyAgent(model, vocab)
    try:
        s1 = single.act(STATES[0], CANDIDATES[0], greedy=True)
        s2 = single.act(STATES[1], CANDIDATES[1], s1.hidden, greedy=True)
        b1 = batched.act(STATES[0], CANDIDATES[0], greedy=True)
        b2 = batched.act(STATES[1], CANDIDATES[1], b1.hidden, greedy=True)
        assert s2.index == b2.index
        assert s2.hidden == pytest.approx(b2.hidden, abs=1e-5)
    finally:
        batched.close()


# A dead server used to strand every caller on an Event that would never be set.
# It happened twice in one day — a NaN weight tripping a device-side assert, then a
# CUDA context invalidated by a driver update mid-run — and each time a crashed
# thread became a silently hung trainer holding twelve idle engine processes.
def test_a_dead_inference_server_fails_workers_instead_of_hanging_them():
    from sts2rl.batch_inference import InferenceServerError

    model = make_model()
    agent = BatchedAgent(model, EntityVocab({}))
    try:
        # whatever kills the server thread — here, a poisoned forward
        def explode(_batch):
            raise RuntimeError("CUDA error: unknown error")

        agent._run_batch = explode  # type: ignore[method-assign]

        with pytest.raises(InferenceServerError):
            agent.act(STATES[0], CANDIDATES[0])

        # and every worker that queues up *after* the death must fail fast too,
        # rather than block forever on a server that will never answer
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(agent.act, STATES[i], CANDIDATES[i]) for i in range(4)]
            for future in futures:
                with pytest.raises(InferenceServerError):
                    future.result(timeout=10)
    finally:
        agent.close()
