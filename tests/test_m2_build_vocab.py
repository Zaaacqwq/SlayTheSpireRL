from __future__ import annotations

import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "m2_build_vocab", REPO_ROOT / "tools" / "m2_build_vocab.py"
)
builder = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(builder)


def test_localization_catalog_covers_complete_slippery_bridge_chain():
    localization = json.loads(builder.EVENT_LOCALIZATION_PATH.read_text(encoding="utf-8"))
    keys = builder.localized_option_keys(localization)
    expected = {
        "SLIPPERY_BRIDGE.pages.INITIAL.options.HOLD_ON_0",
        *(f"SLIPPERY_BRIDGE.pages.HOLD_ON_{index}.options.HOLD_ON_{index + 1}"
          for index in range(6)),
        "SLIPPERY_BRIDGE.pages.HOLD_ON_6.options.HOLD_ON_LOOP",
        "SLIPPERY_BRIDGE.pages.HOLD_ON_LOOP.options.HOLD_ON_LOOP",
    }
    assert expected <= keys


def test_localization_catalog_ignores_page_titles_and_descriptions():
    assert builder.localized_option_keys({
        "EVENT.pages.INITIAL.options.GO.title": "Go",
        "EVENT.pages.INITIAL.options.GO.description": "Go now",
        "EVENT.pages.INITIAL.title": "Initial",
    }) == {"EVENT.pages.INITIAL.options.GO"}
