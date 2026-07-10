"""Fail when an RL-affecting diff omits plan/rl_v2_current_stage.md."""
from __future__ import annotations

import argparse
import subprocess


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("base", nargs="?", default="HEAD^1")
    args = parser.parse_args()
    output = subprocess.check_output(["git", "diff", "--name-only", f"{args.base}...HEAD"], text=True)
    changed = set(output.splitlines())
    affects_rl = any(path.startswith("rl/") or path.startswith("external/sts2-cli") or path in {".gitmodules", "mod/McpMod.StateBuilder.cs", "mcp/server.py"} for path in changed)
    plan_updated = "plan/rl_v2_current_stage.md" in changed
    if affects_rl and not plan_updated:
        print("RL or engine-adapter changes require plan/rl_v2_current_stage.md")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
