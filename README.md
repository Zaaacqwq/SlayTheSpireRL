# Slay the Spire 2 — Reinforcement Learning (RL v2)

A reinforcement-learning agent that learns to play **Slay the Spire 2** on the
*real* game engine — no reimplemented rules — trained stage by stage up a
curriculum and measured only by win rate on held-out seeds. Ships with a bridge
that drives the actual graphical game through the Model Context Protocol (MCP),
so a trained policy (or a person, or Claude) can play through the real UI.

> Status: **M0 & M1 complete · M2 (Ironclad) in progress**. Authoritative roadmap,
> stage status, and acceptance gates live in [`plan/`](plan/).

---

## Architecture

```
                 ActionCandidate │        │ DecisionState / StepResult
                                 ▼        │
  ┌───────────────────────────────────────────────────────────────┐
  │  Real engine  ·  external/sts2-cli (pinned submodule)          │
  │  Sts2Headless.dll  —  one persistent subprocess per worker     │
  └───────────────────────────▲───────────────────┬───────────────┘
                              │                   │
  ┌───────────────────────────┴───────────────────▼───────────────┐
  │  sts2rl  (rl/src)                                              │
  │   EngineClient  ·  observation + candidate features           │
  │   mutable-entity + GRU + Transformer pointer/value policy      │
  │   recurrent masked PPO + GAE  ·  curriculum  ·  telemetry      │
  └───────────────┬───────────────────────────────▲───────────────┘
      history.jsonl│  episodes/*.parquet            │ config.json · checkpoints
      ┌────────────▼───────────┐          ┌─────────┴──────────┐
      │ tools/rl_dashboard.py  │          │ tools/train_watchdog│
      │  (live web dashboard)  │          │ + train_control.py  │
      └────────────────────────┘          └─────────────────────┘

  Deploy (M6):  trained policy ─► mcp/ server ─► mod/ (in-game) ─► graphical STS2
```

Two abstractions carry everything: `EngineClient.reset(RunConfig) → DecisionState`
and `.step(ActionCandidate) → StepResult`. The policy only ever sees the
candidates the engine actually offers, and the trajectory records only those, so
PPO's re-scored log-probs stay consistent with what was sampled.

---

## Project stats

| Metric | Value |
|--------|-------|
| Core library (`rl/src/sts2rl`) | 26 modules · ~3,200 lines Python |
| CLI tools (`tools/`) | 24 scripts · ~5,100 lines Python |
| Dashboard (`dashboard/src`) | 28 files · ~3,700 lines TS/TSX/CSS |
| In-game mod (`mod/`) | 11 C# files (`STS2_MCP`) |
| Tests | 138 Python test functions + Vitest dashboard suites |
| Entity vocabulary | 2,605 game entities (cards, monsters, relics, powers, potions) |
| Curriculum stages | 5 (`normal_combat → mixed_combat → act1 → acts_12 → full_a0`) |
| Engine throughput | 122.81 decision steps/s at 8 workers (M0 benchmark, 0 errors) |
| Target characters | Ironclad (M2) → Silent, Defect, Necrobinder, Regent (M3) |

---

## Quick start

### Prerequisites

- **Python ≥ 3.10** (trainer), **.NET SDK** (headless engine), a CUDA GPU is
  recommended for training.
- A local install of **Slay the Spire 2** and its data directory. Nothing from the
  game is bundled or redistributed — you supply your own copy.

### Install

```bash
# Clone with the engine submodule
git clone --recurse-submodules https://github.com/Zaaacqwq/SlayTheSpireRL.git
cd SlayTheSpireRL

# Build the headless engine (see external/sts2-cli/README)
#   -> external/sts2-cli/src/Sts2Headless/bin/Debug/net9.0/Sts2Headless.dll

# Python env for the trainer
python -m venv rl/.venv
rl/.venv/Scripts/pip install -e "rl[train,dev]"   # torch, pyarrow, pytest

# Point the engine at the game data
export STS2_GAME_DIR=".../Slay the Spire 2/data_sts2_windows_x86_64"
```

### Train

Training runs under a watchdog that auto-restarts on crash and resumes the newest
*intact* checkpoint (a corrupt `resume.pt` is skipped, not fatal). Each run lives
in `rl/runs/<name>/` and is fully described by its `config.json`.

```bash
# start / status / pause a run (reads rl/runs/<name>/config.json)
python tools/train_control.py start  --run-name my_run
python tools/train_control.py status --run-name my_run
python tools/train_control.py pause  --run-name my_run
```

Or invoke the trainer directly for a fresh run:

```bash
python tools/m2_train.py --run-name my_run --workers 12 --device cuda \
    --stage acts_12 --max-stage acts_12 \
    --entropy-coef 0.003 --act-clear-bonus 1.0 --boss-mix 0.15
```

Key flags: `--stage/--max-stage` (curriculum window), `--workers`,
`--episodes-per-iteration`, `--entropy-coef`, `--act-clear-bonus`,
`--hp-shaping-coef`, `--smith-bonus`, `--remove-bonus`, `--boss-mix`,
`--target-kl/--hard-kl-stop`. See `python tools/m2_train.py --help`.

### Monitor

```bash
python tools/rl_dashboard.py          # http://127.0.0.1:8000
```

The dashboard reads every run's `history.jsonl` live: win-rate and floor curves,
reward-health (win vs loss return), the per-act boss funnel (reached vs cleared
Act N), the action mix, and episode replays. Every chart's legend is a click
filter. Rebuild the frontend with `npm --prefix dashboard run build` after editing
`dashboard/src`.

### Play the real game (deploy bridge)

```bash
pwsh mod/build.ps1          # build the STS2_MCP mod
pwsh tools/deploy_mod.ps1   # install it into the game
# launch STS2, then run the MCP server (mcp/) and connect an agent
```

See [`docs/HOW_TO_CONNECT.md`](docs/HOW_TO_CONNECT.md) and
[`mcp/README.md`](mcp/README.md) for the tool surface
(`get_game_state`, `combat_play_card`, `map_choose_node`, `rest_choose_option`, …).

---

## Project structure

```
SlayTheSpireRL/
├── rl/                         RL training library and data
│   ├── src/sts2rl/             the sts2rl package
│   │   ├── engine.py           EngineClient: per-worker subprocess protocol
│   │   ├── protocol.py         DecisionState · ActionCandidate · StepResult
│   │   ├── observation.py      state normalization
│   │   ├── entities.py         entity vocabulary + batch encoding
│   │   ├── features.py         dynamic-candidate feature encoding
│   │   ├── model.py            EntityRecurrentPolicy (entity → GRU → Transformer → pointer/value)
│   │   ├── agent.py            PolicyAgent (single-step inference)
│   │   ├── batch_inference.py  BatchedAgent (batched GPU inference across workers)
│   │   ├── ppo.py              rollout, reward shaping, PPO update
│   │   ├── losses.py           GAE + clipped PPO loss
│   │   ├── curriculum.py       stages, encounter sampling, boss loadouts
│   │   ├── checkpoint.py       atomic (fsync) save / validated load
│   │   ├── telemetry.py        action_mix · depth_profile · reward_health
│   │   ├── visibility.py       offered-vs-chosen action audit
│   │   ├── artifacts.py        episode Parquet + incremental history writer
│   │   ├── evaluator.py        held-out dev evaluation
│   │   └── seeds.py            SHA-256 train/dev/test split
│   ├── schema/                 frozen vocab, boss loadouts, baseline hashes
│   ├── seeds/                  the frozen seed split
│   ├── tests/                  library unit tests
│   └── runs/                   checkpoints · history · episodes (gitignored)
├── tools/                      training, monitoring, evaluation, data-prep CLIs
├── dashboard/                  Vite + React + TS training dashboard (source)
├── external/sts2-cli/          pinned submodule: the headless engine
├── mcp/                        MCP server exposing the live graphical game
├── mod/                        C# STS2_MCP mod (in-game half of the bridge)
├── plan/                       roadmap · current stage · timeline (authoritative)
├── docs/                       contributor + protocol docs
└── tests/                      repo-level tooling tests
```

---

## Curriculum & milestones

Training climbs a fixed ladder; a stage advances only when the held-out
development win rate clears its gate. Capability is judged solely on unseen seeds.

| Stage | What it trains |
|-------|----------------|
| `normal_combat` | single weak/regular fights |
| `mixed_combat` | fights including elites |
| `act1` | a full Act-1 run to the boss |
| `acts_12` | Acts 1–2 (current M2 focus) |
| `full_a0` | the full unascended run |

| Milestone | Scope | Status |
|-----------|-------|--------|
| **M0** | Engine validation, schema freeze, throughput/recovery benchmarks | ✅ done |
| **M1** | Process pool, Gymnasium env, Transformer/GRU pointer policy, PPO/GAE, checkpointing, eval | ✅ done |
| **M2** | Ironclad up the curriculum; ≥40% A0 win rate on 1,000 isolated test seeds | 🚧 in progress |
| **M3** | Silent · Defect · Necrobinder · Regent (reuse M2 curriculum) | ⬜ |
| **M4** | One unified five-character model (distillation + multitask PPO) | ⬜ |
| **M5** | Ascension A1–A10 | ⬜ |
| **M6** | Graphical deployment via the MCP bridge | ⬜ |

---

## How it works

**Real-engine backend.** Training samples from the actual game, not a
reimplementation. Each worker holds one persistent engine subprocess restarted on
timeout; the `external/sts2-cli` submodule is pinned to a specific commit so runs
are reproducible.

**Policy.** A mutable-entity encoder + phase embedding feed a GRU history, a
Transformer, and a dynamic-candidate **pointer head** plus a value head. Unknown
state fields fall into `UNK` with a warning that production training escalates to
fail-fast, so the model never silently trains on states it cannot see.

**Algorithm.** A custom **recurrent, action-masked PPO** (γ 0.999, GAE 0.95,
clip 0.2, AdamW). Recurrent hidden states are stored detached and replayed as
inputs during updates — no backprop through time.

**Reward design.**
- Terminal **+1 win / −1 loss**.
- **Potential-based floor shaping** (`0.2·(γ·Φ′−Φ)`) for run stages — policy-
  invariant, so it densifies progress without changing the optimum. (A non-
  potential version once taught the agent to *walk to the boss and die*; the
  potential form makes that impossible.)
- **Act-clear bonus** on beating an act boss, the dense signal potential shaping
  cannot give on a multi-act stage.
- *(experiment)* **Concave potential-based HP shaping** plus small
  `--smith-bonus` / `--remove-bonus` nudges for the rest-site and shop decisions
  the policy otherwise collapses on. See the branch layout below.

**Seeds.** SHA-256 splits seeds into train / development / test. The frozen
1,000-seed test set never influences selection.

## Branch layout

`main` is the stable base. Current lines of work:

- **`feat/act2-training`** — the clean bug-fixed baseline: per-act boss-funnel
  telemetry and `train_control` start/pause, atop the checkpoint-corruption
  recovery fix.
- **`feat/hp-shaping`** — layered on the baseline: concave potential-based HP
  shaping + `--smith-bonus` / `--remove-bonus`. An isolated reward experiment
  (all flags default off) so it can be A/B'd against the baseline and dropped
  without touching it.

---

## Documentation

| Document | Contents |
|----------|----------|
| [`plan/rl_v2_roadmap.md`](plan/rl_v2_roadmap.md) | Authoritative roadmap, architecture decisions, milestone gates |
| [`plan/rl_v2_current_stage.md`](plan/rl_v2_current_stage.md) | Current-stage tasks, evidence, blockers, next steps |
| [`plan/timeline.md`](plan/timeline.md) | Append-only ledger of every milestone / stage / training rework |
| [`plan/known_gaps_backlog.md`](plan/known_gaps_backlog.md) | Known gaps and backlog |
| [`docs/HOW_TO_CONNECT.md`](docs/HOW_TO_CONNECT.md) | Connecting an agent to the graphical game |
| [`docs/RL_V2_CLI_STATE_MACHINE.md`](docs/RL_V2_CLI_STATE_MACHINE.md) | Engine CLI protocol / state machine |
| [`docs/CONTRIBUTING.md`](docs/CONTRIBUTING.md) | Contribution guidelines |
| [`mcp/README.md`](mcp/README.md) | MCP play-bridge tool reference |

The `plan/` docs are the source of truth; CI (`tools/check_rl_plan_sync.py`)
enforces that code and plan stay in sync.

---

## License

See [`LICENSE`](LICENSE). The engine (`external/sts2-cli`) is a pinned fork based
on its own upstream MIT license; game assets and `sts2.dll` are **not** included
or redistributed. For research and educational use.

## Acknowledgments

- **[`external/sts2-cli`](https://github.com/Zaaacqwq/sts2-cli)** — the headless
  engine harness the trainer runs against.
- **PyTorch** for the policy and PPO; **Vite/React/Recharts** for the dashboard.
