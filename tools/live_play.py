"""Live bridge: drive the real (modded) Slay the Spire 2 game with a trained RL
checkpoint, for a FULL act-1 run. Proves the model can play the actual game.

The key idea (validated by the spike): translate each live mod-JSON decision into
the exact headless-protocol `raw` dict the RL stack already consumes, then reuse
everything downstream unchanged — candidate derivation (protocol.legal_actions),
observation encoding (normalize_state), the policy, and the NoopGuard. The only
bespoke code per phase is (1) live JSON -> headless raw, and (2) the chosen
ActionCandidate -> MCP POST body.

Entity-ID rule (spike-confirmed): the mod emits bare canonical names; the vocab
uses TYPE.NAME. Prefix per kind, and strip an enemy's _<n> instance suffix.

Usage:
    STS2_GAME_DIR=... python tools/live_play.py <checkpoint.pt> [--max-steps 400]
The modded game must be running (HTTP on localhost:15526) and in/near a run.
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

# --- primitives ---
def _int(x, default=0):
    try:
        return int(x)
    except (TypeError, ValueError):
        return default

def _prefix(kind: str, raw_id):
    s = str(raw_id)
    pre = {"card": "CARD.", "relic": "RELIC.", "potion": "POTION.", "enemy": "MONSTER.",
           "power": "POWER."}[kind]
    if kind == "enemy":
        s = re.sub(r"_\d+$", "", s)
    return s if s.startswith(pre) else pre + s

def get_state() -> dict:
    with urllib.request.urlopen(BASE + "?format=json", timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))

def post(body: dict) -> None:
    req = urllib.request.Request(BASE, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        r.read()

def _status_to_power(s: dict) -> dict:
    pid = s.get("id") or s.get("name") or s.get("type") or "UNKNOWN"
    return {"id": _prefix("power", pid), "amount": _int(s.get("amount", s.get("stacks", 0)))}

def _player_block(live: dict) -> dict:
    p = live.get("player", {})
    return {
        "hp": _int(p.get("hp")), "max_hp": _int(p.get("max_hp")),
        "block": _int(p.get("block")), "gold": _int(p.get("gold")),
        "relics": [{"index": i, "id": _prefix("relic", r.get("id"))}
                   for i, r in enumerate(p.get("relics") or [])],
        "potions": [{"index": i, "id": _prefix("potion", pot.get("id")),
                     "target_type": pot.get("target_type")}
                    for i, pot in enumerate(p.get("potions") or [])],
    }

def _ctx_block(live: dict) -> dict:
    run = live.get("run", {})
    return {"act": _int(run.get("act")), "floor": _int(run.get("floor"))}

# --- per-phase translators: live JSON -> (headless raw, ctx) ---
def tr_combat(live: dict):
    b, p = live["battle"], live["player"]
    enemy_ids, enemies = [], []
    for i, e in enumerate(b.get("enemies", [])):
        enemy_ids.append(e.get("entity_id"))
        enemies.append({
            "index": i, "id": _prefix("enemy", e.get("entity_id")),
            "hp": _int(e.get("hp")), "max_hp": _int(e.get("max_hp")), "block": _int(e.get("block")),
            "powers": [_status_to_power(s) for s in (e.get("status") or [])],
            "intents": [{"type": it.get("type", "UNKNOWN")} for it in (e.get("intents") or [])],
        })
    hand = [{"index": _int(c.get("index")), "id": _prefix("card", c.get("id")),
             "cost": _int(c.get("cost")), "type": c.get("type"),
             "target_type": c.get("target_type"), "can_play": bool(c.get("can_play")),
             "upgraded": bool(c.get("is_upgraded"))} for c in p.get("hand", [])]
    pb = _player_block(live)
    pb["deck_size"] = (_int(p.get("draw_pile_count")) + _int(p.get("discard_pile_count"))
                       + len(hand) + _int(p.get("exhaust_pile_count")))
    raw = {
        "type": "decision", "decision": "combat_play", "context": _ctx_block(live),
        "round": _int(b.get("round")), "energy": _int(p.get("energy")),
        "max_energy": _int(p.get("max_energy")),
        "draw_pile_count": _int(p.get("draw_pile_count")),
        "discard_pile_count": _int(p.get("discard_pile_count")),
        "player": pb, "hand": hand, "enemies": enemies,
        "player_powers": [_status_to_power(s) for s in (p.get("status") or [])],
    }
    return raw, {"enemy_ids": enemy_ids}

def tr_map(live: dict):
    opts = live.get("map", {}).get("next_options", [])
    nodes, by_pos = [], {}
    for o in opts:
        col, row = _int(o.get("col")), _int(o.get("row"))
        nodes.append({"col": col, "row": row, "type": o.get("type")})
        by_pos[(col, row)] = o.get("index")
    raw = {"type": "decision", "decision": "map_select", "context": _ctx_block(live),
           "player": _player_block(live), "available_nodes": nodes}
    return raw, {"nodes": by_pos}

def tr_card_reward(live: dict):
    cr = live.get("card_reward", {})
    cards = [{"index": _int(c.get("index")), "id": _prefix("card", c.get("id")),
              "cost": _int(c.get("cost")), "type": c.get("type"),
              "upgraded": bool(c.get("is_upgraded")), "rarity": c.get("rarity")}
             for c in cr.get("cards", [])]
    raw = {"type": "decision", "decision": "card_reward", "context": _ctx_block(live),
           "player": _player_block(live), "cards": cards,
           "can_skip": bool(cr.get("can_skip", True)), "from_event": False}
    return raw, {}

def tr_event(live: dict):
    ev = live.get("event", {})
    options = [{"index": _int(o.get("index")), "is_locked": bool(o.get("is_locked")),
                "is_enabled": not o.get("is_locked", False)}
               for o in ev.get("options", [])]
    raw = {"type": "decision", "decision": "event_choice", "context": _ctx_block(live),
           "player": _player_block(live), "options": options,
           "event_id": ev.get("event_id"), "event_name": ev.get("event_name")}
    return raw, {}

def tr_shop(live: dict):
    items = live.get("shop", {}).get("items", [])
    cats = {"card": [], "relic": [], "potion": []}
    flat = {}  # (headless_action, per_cat_index) -> live flat item index
    kmap = {"card": ("cards", "buy_card"), "relic": ("relics", "buy_relic"),
            "potion": ("potions", "buy_potion")}
    buckets = {"cards": [], "relics": [], "potions": []}
    for it in items:
        cat = it.get("category")
        if cat not in kmap:
            continue
        bucket_key, action = kmap[cat]
        pos = len(buckets[bucket_key])
        rid = it.get(f"{cat}_id") or it.get("id")
        buckets[bucket_key].append({
            "index": pos, "id": _prefix(cat, rid),
            "affordable": bool(it.get("can_afford")) and bool(it.get("is_stocked", True)),
            "target_type": it.get("target_type"),
        })
        flat[(action, pos)] = it.get("index")
    raw = {"type": "decision", "decision": "shop", "context": _ctx_block(live),
           "player": _player_block(live), **buckets, "can_remove_card": False}
    return raw, {"flat": flat}

def tr_rest(live: dict):
    rs = live.get("rest_site", {})
    options = [{"index": _int(o.get("index")), "is_locked": False,
                "is_enabled": bool(o.get("is_enabled", True))}
               for o in rs.get("options", [])]
    raw = {"type": "decision", "decision": "rest_site", "context": _ctx_block(live),
           "player": _player_block(live), "options": options}
    return raw, {}

def _tr_select(live: dict, container: str):
    sel = live.get(container, {})
    cards = [{"index": _int(c.get("index")), "id": _prefix("card", c.get("id")),
              "cost": _int(c.get("cost")), "type": c.get("type"),
              "upgraded": bool(c.get("is_upgraded"))} for c in sel.get("cards", [])]
    # can_confirm at zero picks => the selection is optional; else one pick is required.
    optional = bool(sel.get("can_confirm"))
    raw = {"type": "decision", "decision": "card_select", "context": _ctx_block(live),
           "player": _player_block(live), "cards": cards,
           "min_select": 0 if optional else 1, "max_select": 1}
    return raw, {}

def tr_hand_select(live: dict):  # in-combat selection (exhaust/discard a card)
    return _tr_select(live, "hand_select")

def tr_card_select(live: dict):  # deck-level selection (event/reward picks a card)
    return _tr_select(live, "card_select")

COMBAT_TYPES = ("monster", "elite", "boss")
TRANSLATORS = {
    "monster": tr_combat, "elite": tr_combat, "boss": tr_combat,
    "map": tr_map, "card_reward": tr_card_reward,
    "event": tr_event, "shop": tr_shop, "rest_site": tr_rest,
    "hand_select": tr_hand_select, "card_select": tr_card_select,
}

# --- chosen ActionCandidate -> MCP POST body ---
def action_to_post(action, live_type: str, ctx: dict) -> dict:
    a, args = action.action, action.args
    if a == "end_turn":
        return {"action": "end_turn"}
    if a == "play_card":
        body = {"action": "play_card", "card_index": args["card_index"]}
        if "target_index" in args:
            body["target"] = ctx["enemy_ids"][args["target_index"]]
        return body
    if a == "use_potion":
        body = {"action": "use_potion", "slot": args["potion_index"]}
        if "target_index" in args:
            body["target"] = ctx["enemy_ids"][args["target_index"]]
        return body
    if a == "select_map_node":
        return {"action": "choose_map_node", "index": ctx["nodes"][(args["col"], args["row"])]}
    if a == "select_card_reward":
        return {"action": "select_card_reward", "card_index": args["card_index"]}
    if a == "skip_card_reward":
        return {"action": "skip_card_reward"}
    if a == "choose_option":
        rest = live_type == "rest_site"
        return {"action": "choose_rest_option" if rest else "choose_event_option",
                "index": args["option_index"]}
    if a in ("buy_card", "buy_relic", "buy_potion"):
        idx = args.get("card_index", args.get("relic_index", args.get("potion_index")))
        return {"action": "shop_purchase", "index": ctx["flat"][(a, idx)]}
    if a == "leave_room":
        return {"action": "proceed"}  # shops leave via proceed
    if a in ("select_cards", "skip_select"):
        # in-combat hand_select uses combat_* actions; deck-level card_select uses plain ones
        if live_type == "hand_select":
            sel, conf, key = "combat_select_card", "combat_confirm_selection", "card_index"
        else:
            sel, conf, key = "select_card", "confirm_selection", "index"
        seq = ([{"action": sel, key: int(i)} for i in str(args["indices"]).split(",") if i != ""]
               if a == "select_cards" else [])
        return seq + [{"action": conf}]
    raise ValueError(f"no MCP mapping for action {a!r} in {live_type!r}")

def describe(action, live) -> str:
    a, args = action.action, action.args
    if a == "end_turn":
        return "结束回合"
    if a == "play_card":
        hand = live["player"]["hand"]; enemies = live["battle"]["enemies"]
        card = next((c for c in hand if _int(c.get("index")) == args["card_index"]), {})
        s = f"打出 [{card.get('name','?')}]"
        return s + (f" → {enemies[args['target_index']].get('name','?')}" if "target_index" in args else "")
    if a == "use_potion":
        return f"使用药水 slot {args['potion_index']}"
    if a == "select_map_node":
        return f"选择地图节点 ({args['col']},{args['row']})"
    if a == "select_card_reward":
        cards = live.get("card_reward", {}).get("cards", [])
        card = next((c for c in cards if _int(c.get("index")) == args["card_index"]), {})
        return f"选牌 [{card.get('name','?')}]"
    if a == "skip_card_reward":
        return "跳过选牌"
    if a == "choose_option":
        opts = (live.get("event", {}).get("options", [])
                or live.get("rest_site", {}).get("options", []))
        opt = next((o for o in opts if _int(o.get("index")) == args["option_index"]), {})
        return f"选项 [{opt.get('title') or opt.get('name','?')}]"
    if a.startswith("buy_"):
        return f"购买 {a} #{list(args.values())[0]}"
    if a == "leave_room":
        return "离开商店"
    if a == "select_cards":
        return f"选牌 [{args.get('indices')}]"
    if a == "skip_select":
        return "不选牌"
    return a

# --- stability: wait for a settled, actionable decision ---
DECISION_TYPES = set(TRANSLATORS)
DONE_TYPES = {"game_over", "death", "victory", "run_complete"}

def auto_advance(live: dict, last_action: str | None) -> bool:
    """Resolve non-decision screens the model doesn't own. Returns True if it acted.

    The rewards screen bundles gold/potion/relic freebies plus an optional card.
    Claim the freebies, then open the card so the model can decide. Once the model
    has resolved that card (selected or skipped) the card stays listed but is
    declined — re-claiming it would loop, so proceed out instead."""
    st = live.get("state_type")
    if st == "rewards":
        items = live.get("rewards", {}).get("items", [])
        for it in items:
            if it.get("type") in ("gold", "potion", "relic"):
                post({"action": "claim_reward", "index": it["index"]}); return True
        card = next((it for it in items if it.get("type") == "card"), None)
        if card is not None and last_action not in ("skip_card_reward", "select_card_reward"):
            post({"action": "claim_reward", "index": card["index"]}); return True  # -> card_reward
        post({"action": "proceed"}); return True
    if st == "treasure":  # take the relic, then leave (relic pickup may open a card_select)
        relics = live.get("treasure", {}).get("relics", [])
        if relics:
            post({"action": "claim_treasure_relic", "index": relics[0]["index"]}); return True
        post({"action": "proceed"}); return True
    return False

def read_decision(last_action: str | None, settle=0.35, max_wait=15.0) -> dict:
    """Poll until a settled decision the model owns, a done state, or a screen we
    auto-advance. For combat right after end_turn, skip not-ready start-of-turn
    frames (energy not refilled / hand not drawn)."""
    deadline = time.time() + max_wait
    prev_sig = None
    while time.time() < deadline:
        live = get_state()
        st = live.get("state_type")
        if st in DONE_TYPES:
            return live
        if st not in DECISION_TYPES:
            auto_advance(live, last_action)
            prev_sig = None; time.sleep(settle); continue
        if st in COMBAT_TYPES:
            b, p = live.get("battle", {}), live.get("player", {})
            if b.get("turn") != "player" or not b.get("is_play_phase", True):
                prev_sig = None; time.sleep(settle); continue
            if last_action == "end_turn" and (_int(p.get("energy")) == 0 or not (p.get("hand") or [])):
                prev_sig = None; time.sleep(settle); continue
        try:
            sig = st + "|" + parse_state(TRANSLATORS[st](live)[0]).state_hash
        except Exception:
            # A decision screen with no derivable candidates is consumed/transitional
            # (e.g. a rested campfire, or an event mid-resolution) — nudge it forward
            # if it advertises a proceed, else just wait for it to settle.
            sub = live.get(st) if isinstance(live.get(st), dict) else {}
            if sub.get("can_proceed"):
                try:
                    post({"action": "proceed"})
                except Exception:
                    pass
            prev_sig = None; time.sleep(settle); continue
        if sig == prev_sig:
            return live
        prev_sig = sig; time.sleep(settle)
    return get_state()

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("checkpoint", type=pathlib.Path)
    ap.add_argument("--hidden", type=int, default=256)
    ap.add_argument("--layers", type=int, default=4)
    ap.add_argument("--heads", type=int, default=8)
    ap.add_argument("--max-steps", type=int, default=500)
    ap.add_argument("--delay", type=float, default=0.6)
    args = ap.parse_args()

    vocab = EntityVocab.load(VOCAB)
    model = EntityRecurrentPolicy(vocab_size=vocab.size, candidate_dim=CANDIDATE_FEATURE_DIM,
                                  hidden=args.hidden, heads=args.heads, layers=args.layers)
    load_checkpoint(args.checkpoint, model); model.eval()
    agent = PolicyAgent(model, vocab)
    guard = NoopGuard()
    hidden = None
    last_action = None

    print(f"[live-play] loaded {args.checkpoint.name}; playing a full run...", flush=True)
    for _ in range(args.max_steps):
        live = read_decision(last_action)
        st = live.get("state_type")
        if st in DONE_TYPES:
            print(f"[live-play] run ended: {st!r}", flush=True)
            break
        if st not in TRANSLATORS:
            print(f"[live-play] stuck at unhandled state {st!r} — stopping.", flush=True)
            break
        raw, ctx = TRANSLATORS[st](live)
        state = parse_state(raw)
        effective = guard.offer(state)
        step = agent.act(state.raw, effective, hidden, greedy=True)
        hidden = step.hidden
        action = effective[step.index]
        floor = raw["context"]["floor"]
        print(f"  [层{floor} {st:11s}] 模型: {describe(action, live)}", flush=True)
        before = state.state_hash
        body = action_to_post(action, st, ctx)
        for b in (body if isinstance(body, list) else [body]):
            post(b)
        last_action = action.action
        if args.delay:
            time.sleep(args.delay)
        # No-op bookkeeping only: any failure to re-hash a (possibly transitional)
        # follow-up state just means "the action moved us on", which is the norm.
        after = before + "_moved"
        try:
            after_live = get_state()
            if after_live.get("state_type") == st:
                after = parse_state(TRANSLATORS[st](after_live)[0]).state_hash
        except Exception:
            pass
        guard.record(before, after, action)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
