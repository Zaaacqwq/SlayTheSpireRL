"""Batched acting: many engine workers, one forward pass per micro-batch.

Per-decision inference with batch size 1 leaves a GPU idle; this server
collects concurrent ``act`` requests from worker threads, pads them into one
batch, runs a single forward, and hands each worker its own result. The
``act`` signature matches ``PolicyAgent`` so rollout code does not change.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import queue
import threading
from typing import Any, Mapping, Sequence

import torch

from .agent import AgentStep
from .entities import EntityVocab, candidate_entity_slots, encode_entity_batch
from .features import encode_candidates
from .model import EntityRecurrentPolicy
from .observation import NormalizedObservation, normalize_state
from .protocol import ActionCandidate


@dataclass
class _Request:
    observation: NormalizedObservation
    candidates: Sequence[ActionCandidate]
    hidden: tuple[float, ...] | None
    greedy: bool
    done: threading.Event = field(default_factory=threading.Event)
    result: AgentStep | None = None


class InferenceServerError(RuntimeError):
    """The batching server died; every worker waiting on it must fail, not hang."""


class BatchedAgent:
    """Thread-safe drop-in for ``PolicyAgent`` backed by a batching server."""

    def __init__(self, model: EntityRecurrentPolicy, vocab: EntityVocab,
                 *, max_batch: int = 32, wait_ms: float = 2.0):
        self.model = model
        self.vocab = vocab
        self.device = next(model.parameters()).device
        self.max_batch = max_batch
        self.wait_s = wait_ms / 1000.0
        self._queue: queue.Queue[_Request] = queue.Queue()
        self._stop = threading.Event()
        self._failure: BaseException | None = None
        self._inflight: list[_Request] = []
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def act(self, raw_state: Mapping[str, Any], candidates: Sequence[ActionCandidate],
            hidden: tuple[float, ...] | None = None, *, greedy: bool = False,
            generator: torch.Generator | None = None) -> AgentStep:
        # A dead server used to strand every caller on an Event that would never be
        # set: the batch in flight got woken, and the eleven workers that queued
        # behind it blocked forever. Twice today (a NaN weight, then a CUDA context
        # invalidated by a driver update mid-run) that turned a crashed thread into
        # a silently hung trainer holding twelve idle engines.
        self._raise_if_dead()
        request = _Request(normalize_state(raw_state), candidates, hidden, greedy)
        self._queue.put(request)
        while not request.done.wait(timeout=0.5):
            self._raise_if_dead()
        if request.result is None:
            self._raise_if_dead()
            raise InferenceServerError("inference produced no result")
        return request.result

    def _raise_if_dead(self) -> None:
        if self._failure is not None:
            raise InferenceServerError("inference server died") from self._failure
        if not self._thread.is_alive() and not self._stop.is_set():
            raise InferenceServerError("inference server thread exited")

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def _serve(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    first = self._queue.get(timeout=0.1)
                except queue.Empty:
                    continue
                batch = [first]
                deadline = threading.Event()
                deadline.wait(self.wait_s)  # let concurrent workers queue up
                while len(batch) < self.max_batch:
                    try:
                        batch.append(self._queue.get_nowait())
                    except queue.Empty:
                        break
                self._inflight = batch
                self._run_batch(batch)
                self._inflight = []
        except BaseException as exc:  # noqa: BLE001 - the failure must reach the workers
            self._failure = exc
            raise
        finally:
            self._stop.set()
            self._abandon_pending()

    def _abandon_pending(self) -> None:
        """Wake everyone still waiting; act() turns that into a raise."""
        for request in self._inflight:
            request.result = None
            request.done.set()
        self._inflight = []
        while True:
            try:
                request = self._queue.get_nowait()
            except queue.Empty:
                return
            request.result = None
            request.done.set()

    def _run_batch(self, batch: list[_Request]) -> None:
        observations = [r.observation for r in batch]
        entities = encode_entity_batch(observations, self.vocab)
        width = max(len(r.candidates) for r in batch)
        candidate_rows, masks, slot_rows, hiddens, globals_ = [], [], [], [], []
        for r in batch:
            encoded = encode_candidates(r.candidates)
            padding = width - encoded.shape[0]
            candidate_rows.append(torch.nn.functional.pad(encoded, (0, 0, 0, padding)))
            masks.append(torch.tensor([True] * encoded.shape[0] + [False] * padding))
            slots = candidate_entity_slots(r.observation, r.candidates)
            slot_rows.append(torch.tensor(slots + [-1] * padding, dtype=torch.long))
            hiddens.append(
                torch.zeros(self.model.hidden_size) if r.hidden is None
                else torch.tensor(r.hidden, dtype=torch.float32)
            )
            globals_.append(torch.tensor(r.observation.global_features, dtype=torch.float32))
        device = self.device
        with torch.no_grad():
            logits, values, new_hidden = self.model(
                torch.stack(globals_).to(device),
                {k: v.to(device) for k, v in entities.items()},
                torch.stack(candidate_rows).to(device),
                torch.stack(masks).to(device),
                candidate_slots=torch.stack(slot_rows).to(device),
                hidden=torch.stack(hiddens).to(device),
            )
            log_probs = torch.log_softmax(logits, dim=-1)
            probs = log_probs.exp()
        for row, request in enumerate(batch):
            count = len(request.candidates)
            row_probs = probs[row, :count]
            if request.greedy:
                index = int(torch.argmax(row_probs))
            else:
                index = int(torch.multinomial(row_probs, 1))
            request.result = AgentStep(
                index,
                float(log_probs[row, index]),
                float(values[row]),
                tuple(new_hidden[row].cpu().tolist()),
            )
            request.done.set()
