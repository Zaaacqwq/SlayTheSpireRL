from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import re
import statistics
import threading
import time
from urllib.parse import parse_qs, unquote, urlparse


REPO_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIST = REPO_ROOT / "dashboard" / "dist"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
os.environ.setdefault("ARROW_NUM_THREADS", "1")

# PyArrow 25 can segfault in its native mimalloc/Parquet metadata path when
# multiple ThreadingHTTPServer workers enter it at once on macOS. Python locks
# cannot recover from that SIGSEGV, so every Arrow operation is serialized.
_PARQUET_READ_LOCK = threading.RLock()
_LEGACY_CACHE_LOCK = threading.Lock()
_LEGACY_CACHE: dict[Path, tuple[tuple[tuple[str, int, int], ...], list[dict]]] = {}
_ENRICH_CACHE_LOCK = threading.Lock()
_ENRICH_CACHE: dict[Path, tuple[tuple[int, int], dict]] = {}


def _read_text(path: Path, attempts: int = 5) -> str:
    """Read a file the trainer may be replacing underneath us.

    The live writer publishes workers.json and compacts worker_NN.jsonl with
    os.replace. On Windows the destination is briefly un-openable during that
    swap, so a plain open() from this polling server raises PermissionError —
    exactly the race that, from the writer's side, used to kill the telemetry
    thread. The window is microseconds, so a short retry closes it.
    """
    for attempt in range(attempts):
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.02 * (attempt + 1))
    raise AssertionError("unreachable")


def _json_load(path: Path):
    return json.loads(_read_text(path))


def _json_lines(path: Path):
    if not path.exists():
        return
    for line in _read_text(path).splitlines():
        text = line.strip()
        if not text or not text.startswith("{"):
            continue
        try:
            yield json.loads(text)
        except json.JSONDecodeError:
            continue


def default_data_root() -> Path:
    # Was hardcoded to one developer's macOS home, which resolves nowhere else.
    if env := os.environ.get("STS2_RL_DASHBOARD_DATA_ROOT"):
        return Path(env)
    for candidate in (Path.cwd() / "rl" / "runs", REPO_ROOT / "rl" / "runs"):
        if candidate.exists():
            return candidate
    return REPO_ROOT / "rl" / "runs"


def _live_run_dir(data_root: Path, run_name: str) -> Path:
    run_dir = (data_root / run_name).resolve()
    if not _inside(data_root, run_dir) or not run_dir.is_dir():
        raise FileNotFoundError(run_name)
    return run_dir


def live_workers(data_root: Path, run_name: str) -> dict:
    """Return the latest atomic worker snapshot, or a compatible disabled state."""
    run_dir = _live_run_dir(data_root, run_name)
    snapshot_path = run_dir / "live" / "workers.json"
    if not snapshot_path.exists():
        config_path = run_dir / "config.json"
        config = _json_load(config_path) if config_path.exists() else {}
        return {
            "enabled": False,
            "session_id": None,
            "updated_at": None,
            "worker_count": int(config.get("workers", 0) or 0),
            "dropped_events": 0,
            "stale": False,
            "workers": [],
        }
    snapshot = _json_load(snapshot_path)
    age_seconds = max(0.0, time.time() - snapshot_path.stat().st_mtime)
    snapshot["age_seconds"] = round(age_seconds, 3)
    snapshot["stale"] = age_seconds > 15.0
    return snapshot


def live_worker_events(
    data_root: Path, run_name: str, worker_id: int, *,
    after: int | None = None, limit: int = 200,
) -> dict:
    run_dir = _live_run_dir(data_root, run_name)
    snapshot = live_workers(data_root, run_name)
    worker_count = int(snapshot.get("worker_count", 0))
    if worker_id < 0 or worker_id >= worker_count:
        raise FileNotFoundError(f"worker {worker_id}")
    limit = max(1, min(int(limit), 500))
    session_id = snapshot.get("session_id")
    path = run_dir / "live" / f"worker_{worker_id:02d}.jsonl"
    rows = [
        row for row in _json_lines(path)
        if row.get("session_id") == session_id and (after is None or int(row.get("seq", 0)) > after)
    ] if path.exists() else []
    rows = rows[:limit] if after is not None else rows[-limit:]
    cursor = int(rows[-1]["seq"]) if rows else int(after or 0)
    return {
        "enabled": bool(snapshot.get("enabled")),
        "session_id": session_id,
        "worker_id": worker_id,
        "items": rows,
        "next_after": cursor,
        "dropped_events": int(snapshot.get("dropped_events", 0)),
        "stale": bool(snapshot.get("stale")),
    }


def _inside(root: Path, path: Path) -> bool:
    root, path = root.resolve(), path.resolve()
    return path == root or root in path.parents


def _asset_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower().removeprefix("card"))


def build_asset_index() -> dict[str, Path]:
    """Index directly readable game images; packed resources use UI fallbacks."""
    game_dir = os.environ.get("STS2_GAME_DIR")
    if not game_dir or not Path(game_dir).is_dir():
        return {}
    index: dict[str, Path] = {}
    for path in Path(game_dir).rglob("*"):
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES:
            index.setdefault(_asset_key(path.stem), path)
    return index


def discover_runs(data_root: Path) -> list[Path]:
    if not data_root.exists():
        return []
    return [entry for entry in sorted(data_root.iterdir(), reverse=True)
            if entry.is_dir() and entry.name != "m1_trajectories" and
            ((entry / "config.json").exists() or (entry / "history.jsonl").exists()
             or any(entry.glob("ckpt_*.pt")))]


def _pivot_scalars(rows: list[dict]) -> list[dict]:
    by_step: dict[int, dict] = {}
    for item in rows:
        if not {"name", "value", "step"} <= item.keys():
            continue
        step = int(item["step"])
        stage, _, metric = str(item["name"]).partition("/")
        row = by_step.setdefault(step, {"iteration": step, "stage": stage})
        row[metric or stage] = item["value"]
    return [by_step[key] for key in sorted(by_step)]


# Nested diagnostic blocks the trainer emits; charts need flat numeric columns.
# `action_mix` is open-ended (one key per action type), so it is prefixed rather
# than enumerated — a series that is flat zero means the policy cannot reach that
# action at all, which is exactly how the potion bug hid for five versions.
_NESTED_METRICS = ("reward_health", "depth")


def _flatten_diagnostics(row: dict, raw: dict) -> None:
    for block in _NESTED_METRICS:
        values = raw.get(block)
        if isinstance(values, dict):
            for key, value in values.items():
                if isinstance(value, bool):
                    row[key] = 1.0 if value else 0.0
                elif isinstance(value, (int, float)):
                    row[key] = value
    mix = raw.get("action_mix")
    if isinstance(mix, dict):
        for action, fraction in mix.items():
            row[f"action_{action}"] = fraction


def load_history_for_run(data_root: Path, run_dir: Path) -> tuple[list[dict], str | None]:
    candidates = [run_dir / "history.jsonl", data_root / f"{run_dir.name}.log",
                  data_root / f"{run_dir.name}.jsonl"]
    for candidate in candidates:
        if candidate.exists():
            rows = list(_json_lines(candidate))
            by_iteration: dict[int, dict] = {}
            for raw in rows:
                if "iteration" not in raw:
                    continue
                iteration = int(raw["iteration"])
                row = by_iteration.setdefault(iteration, {"iteration": iteration})
                if any(key in raw for key in ("train_win_rate", "loss", "avg_floor")):
                    row.update(raw)
                    _flatten_diagnostics(row, raw)
                dev = raw.get("dev")
                if isinstance(dev, dict):
                    for key, value in dev.items():
                        row[f"dev_{key}"] = value
                    if raw.get("stage"):
                        row.setdefault("stage", raw["stage"])
            metric_rows = [by_iteration[key] for key in sorted(by_iteration)
                           if len(by_iteration[key]) > 1]
            if metric_rows:
                return metric_rows, str(candidate)
    scalar_path = run_dir / "tb" / "scalars.jsonl"
    if scalar_path.exists():
        return _pivot_scalars(list(_json_lines(scalar_path))), str(scalar_path)
    return [], None


def _manifest(run_dir: Path) -> list[dict]:
    return list(_json_lines(run_dir / "episodes" / "manifest.jsonl"))


def _legacy_files(data_root: Path) -> list[Path]:
    root = data_root / "m1_trajectories"
    if not root.exists():
        return []
    return sorted([*root.glob("*.parquet"), *root.glob("*.jsonl")])


def _legacy_fingerprint(paths: list[Path]) -> tuple[tuple[str, int, int], ...]:
    return tuple((path.name, stat.st_size, stat.st_mtime_ns)
                 for path in paths if (stat := path.stat()))


def _legacy_episodes(data_root: Path) -> list[dict]:
    cache_key = data_root.resolve()
    paths = _legacy_files(data_root)
    fingerprint = _legacy_fingerprint(paths)
    cached = _LEGACY_CACHE.get(cache_key)
    if cached and cached[0] == fingerprint:
        return [dict(item) for item in cached[1]]
    # Only one request may rebuild an invalidated index. The inner cache check
    # prevents queued requests from repeating the same Parquet scan.
    with _LEGACY_CACHE_LOCK:
        cached = _LEGACY_CACHE.get(cache_key)
        if cached and cached[0] == fingerprint:
            return [dict(item) for item in cached[1]]
        result = []
        for path in paths:
            try:
                summary = summarize_episode(path, preview_only=True)
                summary.update({"split": "legacy", "stage": "m1", "iteration": None,
                                "total_reward": None, "final_floor": summary.get("last_floor")})
                result.append(summary)
            except Exception:
                continue
        _LEGACY_CACHE[cache_key] = (fingerprint, result)
        return [dict(item) for item in result]


def _latest_best(history: list[dict]) -> tuple[dict, dict]:
    if not history:
        return {}, {}
    wins = [row for row in history if isinstance(row.get("train_win_rate"), (int, float))]
    return history[-1], max(wins, key=lambda row: row["train_win_rate"]) if wins else history[-1]


def run_summary(data_root: Path, run_dir: Path) -> dict:
    history, source = load_history_for_run(data_root, run_dir)
    latest, best = _latest_best(history)
    episodes = _manifest(run_dir)
    finished = [row for row in episodes if row.get("outcome") is not None]
    wins = sum(row.get("outcome") is True for row in finished)
    rewards = [float(row["total_reward"]) for row in episodes
               if isinstance(row.get("total_reward"), (int, float))]
    floors = [float(row["final_floor"]) for row in episodes
              if isinstance(row.get("final_floor"), (int, float))]
    config_path = run_dir / "config.json"
    return {
        "name": run_dir.name,
        "config": _json_load(config_path) if config_path.exists() else {},
        "history_source": source,
        "history_count": len(history),
        "episode_count": len(episodes),
        "latest": latest,
        "best": best,
        "stats": {
            "wins": wins, "finished": len(finished),
            "win_rate": wins / len(finished) if finished else latest.get("train_win_rate"),
            "avg_reward": statistics.fmean(rewards) if rewards else None,
            "total_reward": sum(rewards) if rewards else None,
            "avg_floor": statistics.fmean(floors) if floors else latest.get("avg_floor"),
            "errors": sum(bool(row.get("error")) for row in episodes),
            "truncated": sum(bool(row.get("truncated")) for row in episodes),
        },
        "checkpoints": len(list(run_dir.glob("ckpt_*.pt"))),
        "availability": {"metrics": bool(history), "episodes": bool(episodes)},
    }


def list_runs(data_root: Path) -> list[dict]:
    return [run_summary(data_root, run_dir) for run_dir in discover_runs(data_root)]


def run_metrics(data_root: Path, run_name: str, after: int | None = None) -> dict:
    run_dir = data_root / run_name
    if not _inside(data_root, run_dir) or not run_dir.exists():
        raise FileNotFoundError(run_name)
    history, source = load_history_for_run(data_root, run_dir)
    if after is not None:
        history = [row for row in history if int(row.get("iteration", -1)) > after]
    metric_names = sorted({key for row in history for key, value in row.items()
                           if isinstance(value, (int, float)) and key != "iteration"})
    return {"run": run_name, "source": source, "metrics": metric_names, "rows": history}


def _episode_route(rows: list[dict]) -> list[dict]:
    """Floor/room transitions for a run: the at-a-glance path the agent took."""
    route: list[dict] = []
    for row in rows:
        state = row.get("state") or {}
        context = _context(state)
        floor = context.get("floor", state.get("floor"))
        room = context.get("room_type")
        if room == "Map":
            continue
        if route and route[-1]["floor"] == floor and route[-1]["room_type"] == room:
            continue
        route.append({"floor": floor, "room_type": room})
    return route


def _enrich_episode(run_dir: Path, entry: dict) -> dict:
    """Add route/HP/deck summary from the episode file, cached by mtime+size."""
    relative = entry.get("path")
    if not relative or "route" in entry:
        return entry
    path = (run_dir / relative).resolve()
    try:
        stat = path.stat()
    except OSError:
        return entry
    fingerprint = (stat.st_size, stat.st_mtime_ns)
    with _ENRICH_CACHE_LOCK:
        cached = _ENRICH_CACHE.get(path)
    if not cached or cached[0] != fingerprint:
        try:
            rows, _ = load_episode_rows(path)
        except Exception:
            return entry
        last_state = (rows[-1].get("state") or {}) if rows else {}
        player = last_state.get("player") or {}
        extra = {
            "route": _episode_route(rows),
            "final_hp": player.get("hp"),
            "max_hp": player.get("max_hp"),
            "gold": player.get("gold"),
            "deck_size": player.get("deck_size") or len(player.get("deck") or []) or None,
        }
        cached = (fingerprint, extra)
        with _ENRICH_CACHE_LOCK:
            _ENRICH_CACHE[path] = cached
    return {**entry, **cached[1]}


def run_timeline(data_root: Path, run_name: str) -> dict:
    """Episode outcomes grouped by (iteration, split): the learning journey."""
    run_dir = data_root / run_name
    if not _inside(data_root, run_dir) or not run_dir.exists():
        raise FileNotFoundError(run_name)
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in _manifest(run_dir):
        groups[(row.get("iteration"), row.get("split"))].append(row)
    items = []
    for (iteration, split), bucket in sorted(
            groups.items(),
            key=lambda kv: (kv[0][0] if kv[0][0] is not None else -1, str(kv[0][1]))):
        finished = [row for row in bucket if row.get("outcome") is not None]
        wins = sum(row.get("outcome") is True for row in finished)
        rewards = [float(row["total_reward"]) for row in bucket
                   if isinstance(row.get("total_reward"), (int, float))]
        floors = [float(row["final_floor"]) for row in bucket
                  if isinstance(row.get("final_floor"), (int, float))]
        items.append({
            "iteration": iteration, "split": split, "stage": bucket[0].get("stage"),
            "episodes": len(bucket), "wins": wins,
            "win_rate": wins / len(finished) if finished else None,
            "avg_reward": statistics.fmean(rewards) if rewards else None,
            "avg_floor": statistics.fmean(floors) if floors else None,
            "errors": sum(bool(row.get("error")) for row in bucket),
        })
    return {"run": run_name, "items": items}


def list_episodes(data_root: Path, run_name: str, query: dict[str, list[str]]) -> dict:
    if run_name == "legacy":
        rows = _legacy_episodes(data_root)
    else:
        run_dir = data_root / run_name
        if not _inside(data_root, run_dir) or not run_dir.exists():
            raise FileNotFoundError(run_name)
        rows = _manifest(run_dir)
    search = query.get("search", [""])[0].lower()
    split = query.get("split", [""])[0]
    outcome = query.get("outcome", [""])[0]
    stage = query.get("stage", [""])[0]
    iteration = query.get("iteration", [""])[0]
    if search:
        rows = [row for row in rows if search in str(row.get("episode_id", "")).lower()]
    if split:
        rows = [row for row in rows if row.get("split") == split]
    if stage:
        rows = [row for row in rows if row.get("stage") == stage]
    if iteration:
        rows = [row for row in rows if str(row.get("iteration")) == iteration]
    if outcome in {"win", "loss"}:
        expected = outcome == "win"
        rows = [row for row in rows if row.get("outcome") is expected]
    sort_key = query.get("sort", ["newest"])[0]
    rows.sort(key=lambda row: (row.get("iteration") or -1, row.get("episode_id") or ""),
              reverse=sort_key != "oldest")
    total = len(rows)
    page = max(1, int(query.get("page", ["1"])[0]))
    page_size = min(200, max(1, int(query.get("page_size", ["50"])[0])))
    start = (page - 1) * page_size
    page_rows = rows[start:start + page_size]
    if run_name != "legacy":
        run_dir = data_root / run_name
        page_rows = [_enrich_episode(run_dir, row) for row in page_rows]
    return {"items": page_rows, "total": total,
            "page": page, "page_size": page_size}


def load_episode_rows(path: Path) -> tuple[list[dict], str]:
    if path.suffix == ".parquet":
        import pyarrow.parquet as pq
        with _PARQUET_READ_LOCK:
            return pq.read_table(path, use_threads=False).to_pylist(), "parquet"
    if path.suffix in {".jsonl", ".log"}:
        return list(_json_lines(path)), "jsonl"
    payload = _json_load(path)
    if isinstance(payload, list):
        return payload, "json"
    raise ValueError(f"unsupported episode file: {path}")


def _context(state: dict) -> dict:
    return state.get("context") or {}


def summarize_episode(path: Path, preview_only: bool = False) -> dict:
    rows, source = load_episode_rows(path)
    if not rows:
        return {"episode_id": path.stem, "path": str(path), "rows": 0, "steps": 0,
                "source": source}
    first, last = rows[0], rows[-1]
    first_state, last_state = first.get("state") or {}, last.get("state") or {}
    first_context, last_context = _context(first_state), _context(last_state)
    outcome = last.get("outcome")
    if isinstance(outcome, str) and outcome.lower() in {"true", "false"}:
        outcome = outcome.lower() == "true"
    result = {
        "episode_id": first.get("episode_id") or path.stem,
        "path": str(path), "rows": len(rows), "steps": len(rows), "source": source,
        "character": (first_state.get("player") or {}).get("name"),
        "outcome": outcome,
        "first_floor": first_context.get("floor", first_state.get("floor")),
        "last_floor": last_context.get("floor", last_state.get("floor")),
        "first_act": first_context.get("act", first_state.get("act")),
        "last_act": last_context.get("act", last_state.get("act")),
        "first_room": first_context.get("room_type"),
        "last_room": last_context.get("room_type"),
    }
    if not preview_only:
        result["rows_preview"] = [summarize_episode_row(row) for row in rows]
    return result


def summarize_episode_row(row: dict) -> dict:
    state = row.get("state") or {}
    context = _context(state)
    player = state.get("player") or {}
    action = row.get("action") or {}
    args = action.get("args") or {}
    selected = None
    if "card_index" in args and args.get("card_index") is not None:
        candidates = state.get("cards") or state.get("hand") or []
        selected = next((card for card in candidates if card.get("index") == args["card_index"]), None)
    elif "option_index" in args and args.get("option_index") is not None:
        selected = next((option for option in (state.get("options") or [])
                         if option.get("index") == args["option_index"]), None)
    return {
        "step": row.get("step"), "decision": state.get("decision") or state.get("type"),
        "act": context.get("act", state.get("act")),
        "act_name": context.get("act_name", state.get("act_name")),
        "floor": context.get("floor", state.get("floor")),
        "room_type": context.get("room_type"), "round": state.get("round"),
        "action": action, "selected": selected, "legal_actions": row.get("legal_actions") or [],
        "reward": row.get("reward"), "logp": row.get("logp"), "value": row.get("value"),
        "terminated": row.get("terminated"), "outcome": row.get("outcome"),
        "player": player, "cards": state.get("cards"), "options": state.get("options"),
        "choices": state.get("choices"), "hand": state.get("hand"),
        "enemies": state.get("enemies"), "energy": state.get("energy"),
        "max_energy": state.get("max_energy"), "player_powers": state.get("player_powers"),
        "state": state,
    }


def episode_detail(data_root: Path, run_name: str, episode_id: str,
                   query: dict[str, list[str]] | None = None) -> dict:
    """Look up one episode; iteration/split narrow re-recorded seeds."""
    query = query or {}
    iteration = query.get("iteration", [""])[0]
    split = query.get("split", [""])[0]
    candidates: list[tuple[Path, dict]] = []
    if run_name == "legacy":
        for item in _legacy_episodes(data_root):
            candidates.append((Path(item["path"]), item))
    else:
        run_dir = data_root / run_name
        for item in _manifest(run_dir):
            if item.get("path"):
                candidates.append((run_dir / item["path"], item))
    if iteration:
        candidates = [(path, meta) for path, meta in candidates
                      if str(meta.get("iteration")) == iteration]
    if split:
        candidates = [(path, meta) for path, meta in candidates
                      if meta.get("split") == split]
    match = next(((path, meta) for path, meta in candidates
                  if str(meta.get("episode_id")) == episode_id), None)
    if not match or not _inside(data_root, match[0]):
        raise FileNotFoundError(episode_id)
    path, meta = match
    rows, source = load_episode_rows(path)
    return {"meta": meta, "summary": summarize_episode(path, preview_only=True),
            "rows": [summarize_episode_row(row) for row in rows], "source": source}


# Compatibility helpers retained for the original dashboard tests and scripts.
def build_catalog(data_root: Path) -> dict:
    runs = list_runs(data_root)
    return {"data_root": str(data_root), "runs": runs, "episodes": _legacy_episodes(data_root),
            "eval_summaries": []}


def api_run_detail(data_root: Path, run_name: str) -> dict:
    detail = run_summary(data_root, data_root / run_name)
    detail["history"] = load_history_for_run(data_root, data_root / run_name)[0]
    return detail


def api_episode_detail(data_root: Path, rel_path: str) -> dict:
    path = (data_root / rel_path).resolve()
    if not _inside(data_root, path):
        raise ValueError("episode path must stay under the data root")
    rows, source = load_episode_rows(path)
    return {"episode": summarize_episode(path, preview_only=True),
            "rows": [summarize_episode_row(row) for row in rows], "source": source}


def load_eval_summary(path: Path) -> dict:
    payload = list(_json_lines(path)) if path.suffix == ".jsonl" else _json_load(path)
    result = {"file": path.name, "path": str(path), "kind": path.suffix.lstrip("json") or "json"}
    if isinstance(payload, list):
        result.update({"rows": len(payload), "wins": sum(row.get("outcome") is True for row in payload),
                       "errors": sum(bool(row.get("error")) for row in payload)})
        result["win_rate"] = result["wins"] / len(payload) if payload else None
    elif isinstance(payload, dict):
        result.update(payload)
    return result


class DashboardServer(ThreadingHTTPServer):
    def __init__(self, address, handler, data_root: Path):
        super().__init__(address, handler)
        self.data_root = data_root
        self._asset_index: dict[str, Path] | None = None
        # Build legacy metadata before accepting concurrent HTTP requests.
        self.legacy_episode_count = len(_legacy_episodes(data_root))

    def asset(self, name: str) -> Path | None:
        if self._asset_index is None:
            self._asset_index = build_asset_index()
        key = _asset_key(name)
        return self._asset_index.get(key) or next(
            (path for candidate, path in self._asset_index.items()
             if len(key) > 4 and (key in candidate or candidate in key)), None,
        )


class DashboardHandler(BaseHTTPRequestHandler):
    server: DashboardServer

    def log_message(self, format, *args):  # noqa: A003
        return

    def _bytes(self, body: bytes, content_type: str, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # Hashed asset filenames may cache; JSON and index.html must not.
        cacheable = not content_type.startswith(("application/json", "text/html"))
        self.send_header("Cache-Control", "public, max-age=3600" if cacheable else "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload, status=200):
        self._bytes(json.dumps(payload, ensure_ascii=False).encode(), "application/json; charset=utf-8", status)

    def _static(self, path: str):
        relative = path.lstrip("/") or "index.html"
        target = (FRONTEND_DIST / relative).resolve()
        if not _inside(FRONTEND_DIST, target) or not target.is_file():
            target = FRONTEND_DIST / "index.html"
        if not target.exists():
            self._bytes(b"Dashboard frontend is not built. Run npm run build in dashboard/.\n",
                        "text/plain; charset=utf-8", 503)
            return
        self._bytes(target.read_bytes(), mimetypes.guess_type(target.name)[0] or "application/octet-stream")

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        parts = [unquote(part) for part in parsed.path.strip("/").split("/") if part]
        query = parse_qs(parsed.query)
        try:
            if parsed.path == "/api/runs":
                self._json({"items": list_runs(self.server.data_root),
                            "legacy_episode_count": self.server.legacy_episode_count})
            elif len(parts) == 3 and parts[:2] == ["api", "runs"]:
                self._json(run_summary(self.server.data_root, self.server.data_root / parts[2]))
            elif len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "metrics":
                after = int(query["after"][0]) if "after" in query else None
                self._json(run_metrics(self.server.data_root, parts[2], after))
            elif len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "summary":
                self._json(run_summary(self.server.data_root, self.server.data_root / parts[2]))
            elif len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "episodes":
                self._json(list_episodes(self.server.data_root, parts[2], query))
            elif len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "timeline":
                self._json(run_timeline(self.server.data_root, parts[2]))
            elif len(parts) == 5 and parts[:2] == ["api", "runs"] and parts[3:] == ["live", "workers"]:
                self._json(live_workers(self.server.data_root, parts[2]))
            elif (len(parts) == 7 and parts[:2] == ["api", "runs"]
                  and parts[3] == "live" and parts[4] == "workers" and parts[6] == "events"):
                after = int(query["after"][0]) if "after" in query else None
                limit = int(query.get("limit", ["200"])[0])
                self._json(live_worker_events(
                    self.server.data_root, parts[2], int(parts[5]), after=after, limit=limit,
                ))
            elif len(parts) == 5 and parts[:2] == ["api", "runs"] and parts[3] == "episodes":
                self._json(episode_detail(self.server.data_root, parts[2], parts[4], query))
            elif parsed.path == "/api/catalog":
                self._json(build_catalog(self.server.data_root))
            elif parsed.path.startswith("/api/episodes/"):
                self._json(api_episode_detail(self.server.data_root, unquote(parsed.path.removeprefix("/api/episodes/"))))
            elif parsed.path.startswith("/api/runs/") and len(parts) == 3:
                self._json(api_run_detail(self.server.data_root, parts[2]))
            elif len(parts) == 4 and parts[:3] == ["api", "assets", "by-name"]:
                asset = self.server.asset(parts[3])
                if not asset:
                    self._json({"error": "asset not found"}, 404)
                else:
                    self._bytes(asset.read_bytes(), mimetypes.guess_type(asset.name)[0] or "application/octet-stream")
            elif parsed.path.startswith("/api/"):
                self._json({"error": "not found"}, 404)
            else:
                self._static(parsed.path)
        except FileNotFoundError as exc:
            self._json({"error": f"not found: {exc}"}, 404)
        except Exception as exc:  # surfaced to local UI
            self._json({"error": str(exc)}, 400)


def parse_host_port(value: str) -> tuple[str, int]:
    if ":" not in value:
        raise argparse.ArgumentTypeError("expected HOST:PORT")
    host, port = value.rsplit(":", 1)
    return host, int(port)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Live STS2 RL training and replay dashboard")
    parser.add_argument("--data-root", type=Path, default=default_data_root())
    parser.add_argument("--serve", type=parse_host_port, default=("127.0.0.1", 8000))
    args = parser.parse_args(argv)
    if not args.data_root.exists():
        raise SystemExit(f"data root not found: {args.data_root}")
    server = DashboardServer(args.serve, DashboardHandler, args.data_root)
    print(f"dashboard serving on http://{server.server_address[0]}:{server.server_address[1]}")
    print(f"data root: {args.data_root}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
