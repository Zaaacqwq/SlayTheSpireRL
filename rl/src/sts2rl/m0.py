from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys


CLI_COMMIT = "d11aa883b582dd68bd39b331f3370746b30d447e"


def doctor(root: Path) -> int:
    cli = root / "external" / "sts2-cli"
    game_dir = Path(str(__import__("os").environ.get("STS2_GAME_DIR", "")))
    checks = {
        "python_3_10_plus": sys.version_info >= (3, 10),
        "dotnet_available": shutil.which("dotnet") is not None,
        "submodule_initialized": (cli / "src" / "Sts2Headless" / "Sts2Headless.csproj").is_file(),
        "sts2_game_dir_set": bool(str(game_dir)) and str(game_dir) != ".",
        "sts2_dll_present": bool(str(game_dir)) and str(game_dir) != "." and any(game_dir.rglob("sts2.dll")) if game_dir.exists() else False,
        "headless_build_present": (cli / "src" / "Sts2Headless" / "bin").exists(),
    }
    print(json.dumps({"expected_cli_commit": CLI_COMMIT, "checks": checks}, indent=2))
    return 0 if all(checks.values()) else 2


def main() -> None:
    parser = argparse.ArgumentParser(description="RL v2 M0 environment doctor")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[3])
    args = parser.parse_args()
    raise SystemExit(doctor(args.root.resolve()))


if __name__ == "__main__":
    main()
