"""Live bridge: drive the real (modded) Slay the Spire 2 game with a trained RL
checkpoint, for COMBAT. Proves the model can play the actual game.

The key idea (validated by the spike): translate the live mod JSON into the exact
headless-protocol `raw` dict the RL stack already consumes, then reuse everything
downstream unchanged — candidate derivation (protocol.legal_actions), observation
encoding (normalize_state), the policy, and the NoopGuard. The only bespoke code
is (1) live JSON -> headless raw, and (2) chosen ActionCandidate -> MCP POST.

Entity-ID rule discovered in the spike: the mod emits bare canonical names; the
vocab uses TYPE.NAME. So prefix per kind, and strip an enemy's _<n> instance suffix.

Usage:
    STS2_GAME_DIR=... python tools/live_play.py <checkpoint.pt> [--max-turns 40]
The modded game must be running (HTTP on localhost:15526) and in a combat.
"""
from __future__ import annotations
import argparse, json, re, sys, time, urllib.request, pathlib

try:
    sys.stdout.reconfigure(encoding="utf-8")  # card/enemy names are localized (Chinese)
except AttributeError:
    pass

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "rl" / "src"))
from sts2rl.agent import PolicyAgent
from sts2rl.checkpoint import load_checkpoint
from sts2rl.entities import EntityVocab
from sts2rl.features import CANDIDATE_FEATURE_DIM
from sts2rl.model import EntityRecurrentPolicy
from sts2rl.protocol import parse_state
from sts2rl.ppo import NoopGuard

BASE = "http://localhost:15526/api/v1/singleplayer"
VOCAB = REPO / "rl" / "schema" / "m2_vocab.json"

# --- ID prefixing (spike-confirmed) ---
def _prefix(kind: str, raw_id):
    s = str(raw_id)
    pre = {"card": "CARD.", "relic": "RELIC.", "potion": "POTION.", "enemy": "MONSTER.",
           "power": "POWER."}[kind]
    if kind == "enemy":
        s = re.sub(r"_\d+$", "", s)  # strip instance suffix SHRINKER_BEETLE_0 -> SHRINKER_BEETLE
    return s if s.startswith(pre) else pre + s

def _int(x, default=0):
    try:
        return int(x)
    except (TypeError, ValueError):
        return default

# --- HTTP ---
def get_state() -> dict:
    with urllib.request.urlopen(BASE + "?format=json", timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))

def post(body: dict) -> None:
    req = urllib.request.Request(BASE, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        r.read()

def read_stable_combat(settle: float = 0.35, max_wait: float = 12.0,
                       fresh_turn: bool = False) -> dict:
    """Poll until the game presents a *settled* player decision — the same state
    seen twice in a row. Skips enemy-turn animation frames. When ``fresh_turn``
    (used right after an end_turn), it also skips the transient start-of-turn frames
    where the turn has flipped to the player but the turn isn't ready to play yet:
    energy not refilled (0), or cards not drawn (empty hand). Those frames are
    briefly stable, so the hash check alone misses them and the model would waste a
    spurious end_turn on them. Returns early if combat ends."""
    deadline = time.time() + max_wait
    prev = None
    while time.time() < deadline:
        live = get_state()
        if live.get("state_type") != "monster":
            return live
        b = live.get("battle", {})
        if b.get("turn") != "player" or not b.get("is_play_phase", True):
            prev = None
            time.sleep(settle)
            continue
        p = live.get("player", {})
        if fresh_turn and (_int(p.get("energy")) == 0 or not (p.get("hand") or [])):
            prev = None  # new turn not yet ready (energy refilling / cards drawing)
            time.sleep(settle)
            continue
        h = parse_state(live_combat_to_headless(live)[0]).state_hash
        if h == prev:
            return live
        prev = h
        time.sleep(settle)
    return live

# --- live combat JSON -> headless combat_play raw ---
def live_combat_to_headless(live: dict) -> tuple[dict, list[str]]:
    b, p, run = live["battle"], live["player"], live.get("run", {})
    enemy_ids: list[str] = []  # headless index -> live entity_id (for POST target)
    enemies = []
    for i, e in enumerate(b.get("enemies", [])):
        enemy_ids.append(e.get("entity_id"))
        enemies.append({
            "index": i, "id": _prefix("enemy", e.get("entity_id")),
            "hp": _int(e.get("hp")), "max_hp": _int(e.get("max_hp")), "block": _int(e.get("block")),
            "powers": [_status_to_power(s) for s in (e.get("status") or [])],
            "intents": [{"type": it.get("type", "UNKNOWN")} for it in (e.get("intents") or [])],
        })
    hand = []
    for c in p.get("hand", []):
        hand.append({
            "index": _int(c.get("index")), "id": _prefix("card", c.get("id")),
            "cost": _int(c.get("cost")), "type": c.get("type"),
            "target_type": c.get("target_type"), "can_play": bool(c.get("can_play")),
            "upgraded": bool(c.get("is_upgraded")), "name": c.get("name"),
        })
    potions = [{"index": i, "id": _prefix("potion", pot.get("id")),
                "target_type": pot.get("target_type")}
               for i, pot in enumerate(p.get("potions") or [])]
    relics = [{"index": i, "id": _prefix("relic", r.get("id"))}
              for i, r in enumerate(p.get("relics") or [])]
    pile = _int(p.get("draw_pile_count")) + _int(p.get("discard_pile_count")) + \
        len(hand) + _int(p.get("exhaust_pile_count"))
    raw = {
        "type": "decision", "decision": "combat_play",
        "context": {"act": _int(run.get("act")), "floor": _int(run.get("floor"))},
        "round": _int(b.get("round")),
        "energy": _int(p.get("energy")), "max_energy": _int(p.get("max_energy")),
        "draw_pile_count": _int(p.get("draw_pile_count")),
        "discard_pile_count": _int(p.get("discard_pile_count")),
        "player": {
            "hp": _int(p.get("hp")), "max_hp": _int(p.get("max_hp")),
            "block": _int(p.get("block")), "gold": _int(p.get("gold")),
            "deck_size": pile, "relics": relics, "potions": potions,
        },
        "hand": hand, "enemies": enemies,
        "player_powers": [_status_to_power(s) for s in (p.get("status") or [])],
    }
    return raw, enemy_ids

def _status_to_power(s: dict) -> dict:
    # live power/buff format is not fully pinned; map best-effort so unknowns
    # degrade to UNK rather than crash.
    pid = s.get("id") or s.get("name") or s.get("type") or "UNKNOWN"
    return {"id": _prefix("power", pid), "amount": _int(s.get("amount", s.get("stacks", 0)))}

# --- ActionCandidate -> MCP POST body ---
def action_to_post(action, enemy_ids: list[str]) -> dict:
    a, args = action.action, action.args
    if a == "end_turn":
        return {"action": "end_turn"}
    if a == "play_card":
        body = {"action": "play_card", "card_index": args["card_index"]}
        if "target_index" in args:
            body["target"] = enemy_ids[args["target_index"]]
        return body
    if a == "use_potion":
        body = {"action": "use_potion", "slot": args["potion_index"]}
        if "target_index" in args:
            body["target"] = enemy_ids[args["target_index"]]
        return body
    raise ValueError(f"no MCP mapping for action {a!r}")

def describe(action, live) -> str:
    a, args = action.action, action.args
    if a == "end_turn":
        return "结束回合"
    hand = live["player"]["hand"]; enemies = live["battle"]["enemies"]
    if a == "play_card":
        card = next((c for c in hand if _int(c.get("index")) == args["card_index"]), {})
        s = f"打出 [{card.get('name','?')}]"
        if "target_index" in args:
            s += f" → {enemies[args['target_index']].get('name','?')}"
        return s
    if a == "use_potion":
        return f"使用药水 slot {args['potion_index']}"
    return a

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("checkpoint", type=pathlib.Path)
    ap.add_argument("--hidden", type=int, default=256)
    ap.add_argument("--layers", type=int, default=4)
    ap.add_argument("--heads", type=int, default=8)
    ap.add_argument("--max-turns", type=int, default=60)
    ap.add_argument("--delay", type=float, default=1.2)
    args = ap.parse_args()

    vocab = EntityVocab.load(VOCAB)
    model = EntityRecurrentPolicy(vocab_size=vocab.size, candidate_dim=CANDIDATE_FEATURE_DIM,
                                  hidden=args.hidden, heads=args.heads, layers=args.layers)
    load_checkpoint(args.checkpoint, model); model.eval()
    agent = PolicyAgent(model, vocab)
    guard = NoopGuard()
    hidden = None

    print(f"[live-play] loaded {args.checkpoint.name}; waiting for a settled combat decision...", flush=True)
    live = read_stable_combat()
    for _ in range(args.max_turns):
        st = live.get("state_type")
        if st != "monster":
            print(f"[live-play] combat over (state_type={st!r}) — stopping.", flush=True)
            break
        raw, enemy_ids = live_combat_to_headless(live)
        state = parse_state(raw)
        effective = guard.offer(state)
        step = agent.act(state.raw, effective, hidden, greedy=True)
        hidden = step.hidden
        action = effective[step.index]
        e = live["battle"]["enemies"][0]
        print(f"  回合{live['battle'].get('round')} | 能量 {raw['energy']}/{raw['max_energy']} | "
              f"HP {raw['player']['hp']} | 敌 {e.get('name')} HP {e.get('hp')} "
              f"→ 模型: {describe(action, live)}", flush=True)
        before = state.state_hash
        post(action_to_post(action, enemy_ids))
        if args.delay:
            time.sleep(args.delay)
        # After ending a turn, wait past the enemy turn AND the not-ready start-of-turn frames.
        live = read_stable_combat(fresh_turn=(action.action == "end_turn"))
        after = (parse_state(live_combat_to_headless(live)[0]).state_hash
                 if live.get("state_type") == "monster" else before + "_end")
        guard.record(before, after, action)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
