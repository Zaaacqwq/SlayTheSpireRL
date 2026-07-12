#!/bin/zsh
# M2 P4 sequential training queue: waits for the currently running init0,
# then trains inits 1-4 and the terminal-only reward ablation, one at a time
# (each trainer already saturates the engine workers).
set -u
cd "$(dirname "$0")/.."

while pgrep -f "m2_train.py --run-name m2_v4_init0" > /dev/null; do sleep 60; done

for k in 1 2 3 4; do
  if [ ! -f "rl/runs/m2_v4_init$k/history.jsonl" ]; then
    rl/.venv/bin/python -u tools/m2_train.py --run-name "m2_v4_init$k" --init-seed "$k" \
      --iterations 900 --episodes-per-iteration 48 --workers 6 \
      --eval-every 10 --eval-episodes 50 > "rl/runs/m2_v4_init$k.log" 2>&1
  fi
done

if [ ! -f rl/runs/m2_v4_ablation/history.jsonl ]; then
  rl/.venv/bin/python -u tools/m2_train.py --run-name m2_v4_ablation --init-seed 0 --terminal-only \
    --iterations 900 --episodes-per-iteration 48 --workers 6 \
    --eval-every 10 --eval-episodes 50 > rl/runs/m2_v4_ablation.log 2>&1
fi
echo "queue complete"
