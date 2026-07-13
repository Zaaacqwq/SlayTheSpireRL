#!/bin/zsh
# Deck-observation experiment: waits for the whole m2_v4 queue to finish, then
# trains m2_v5_deckobs from THIS worktree (deck entities in the observation)
# against the primary checkout's engine build. Runs and log land in the primary
# checkout's rl/runs so the dashboard picks them up live.
set -u
cd "$(dirname "$0")/.."
MAIN=/Users/zaaac/Documents/Code/SlayTheSpireRL

while pgrep -f "m2_train.py --run-name m2_v4" > /dev/null \
   || pgrep -f "m2_train_queue.sh" > /dev/null; do
  sleep 300
done

if [ ! -f "$MAIN/rl/runs/m2_v5_deckobs/history.jsonl" ]; then
  STS2_CLI_ROOT="$MAIN/external/sts2-cli" \
  "$MAIN/rl/.venv/bin/python" -u tools/m2_train.py --run-name m2_v5_deckobs --init-seed 0 \
    --iterations 900 --episodes-per-iteration 48 --workers 6 \
    --eval-every 10 --eval-episodes 50 \
    --runs-root "$MAIN/rl/runs" > "$MAIN/rl/runs/m2_v5_deckobs.log" 2>&1
fi
echo "m2_v5_deckobs complete"
