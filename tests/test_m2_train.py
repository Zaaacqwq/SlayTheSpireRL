from __future__ import annotations

import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "m2_train", REPO_ROOT / "tools" / "m2_train.py"
)
trainer = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(trainer)


def test_passing_gate_is_persisted_as_the_resumed_stage():
    assert trainer.stage_index_after_evaluation(1, 2, "mixed_combat", 0.80) == 2


def test_failing_or_capped_gate_keeps_the_current_stage():
    assert trainer.stage_index_after_evaluation(1, 2, "mixed_combat", 0.59) == 1
    assert trainer.stage_index_after_evaluation(2, 2, "act1", 1.0) == 2
