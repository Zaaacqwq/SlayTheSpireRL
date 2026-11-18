"""Per-encounter win rates: which fights actually kill the agent.

``depth_profile`` says *where* runs end (floor 14, act 2) but never *what* ended
them, so a single lethal encounter hides inside an aggregate win rate. Splitting
recorded episodes into combats and scoring each one turns "act 2 is hard" into
"Decimillipede x3 is a coin flip and The Insatiable eats 45% of the runs that
reach it" — and it makes engine bugs loud: a boss that spawns no enemies shows up
as a 100%-win fight with zero HP lost (the KAISER_CRAB free win).

The trainer cannot produce this without a restart, so it is derived from the
Parquet episodes already on disk. Reading only the four nested columns the
segmenter needs is ~5x cheaper than a whole episode, and every file is parsed at
most once: results are memoised to ``.dashboard_cache/encounters.jsonl`` keyed by
(size, mtime), so a run costs one background pass and then only its new episodes.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import json
from pathlib import Path
import threading
from typing import Any, Sequence

# PyArrow 25 can segfault in its native Parquet metadata path when several
# threads enter it at once, and a SIGSEGV is not recoverable from Python. The
# dashboard shares this one lock across every Arrow call (see rl_dashboard).
PARQUET_LOCK = threading.RLock()

CACHE_DIRNAME = ".dashboard_cache"
CACHE_FILENAME = "encounters.jsonl"

# Room type -> encounter class. Everything else (Shop, Event, RestSite, Map) is
# not a fight and never opens a segment.
COMBAT_ROOMS = {"Monster": "monster", "Elite": "elite", "Boss": "boss"}
KINDS = ("monster", "elite", "boss")

# The segmenter only needs where we are, who is on the field, and the HP bar.
# Projecting the whole `state` struct instead pulls the deck and every card's
# description on every step, which costs ~5x more per file.
_COLUMNS = ("step", "state.context", "state.enemies", "state.player.hp")


@dataclass
class Combat:
    """One fight inside one episode."""

    kind: str
    label: str
    enemies: tuple[str, ...]
    act: int | None
    floor: float | None
    result: str  # win | loss | unresolved
    hp_in: int | None = None
    hp_out: int | None = None

    def as_dict(self) -> dict[str, Any]:
        row = {"kind": self.kind, "label": self.label, "enemies": list(self.enemies),
               "act": self.act, "floor": self.floor, "result": self.result}
        if self.hp_in is not None:
            row["hp_in"] = self.hp_in
            row["hp_out"] = self.hp_out
        return row


def _enemy_label(enemies: Sequence[dict]) -> tuple[str, tuple[str, ...]]:
    """A stable name for a spawn group: ``Bowlbug (Egg) + Twig Slime x2``.

    Counted rather than listed so the three-Exoskeleton fight and the one-
    Exoskeleton fight (different difficulty, different win rate) stay distinct.
    """
    names = Counter(str(e.get("name") or e.get("id") or "?") for e in enemies)
    label = " + ".join(f"{name} x{count}" if count > 1 else name
                       for name, count in sorted(names.items()))
    return label, tuple(sorted(names))


def episode_combats(rows: Sequence[dict], outcome: bool | None) -> list[Combat]:
    """Split one episode's decision rows into the fights it contains.

    A fight is the contiguous stretch of decisions inside one combat room, keyed
    by (act, floor) because a run never revisits a floor. The room stays the same
    for the post-combat card reward, so HP is sampled only from rows that still
    have enemies on the field — otherwise Burning Blood's end-of-combat heal
    lands inside the fight and understates its cost.

    Outcomes are inferred, not recorded: leaving a combat room at all means the
    fight was won, so only the fight the episode *ends inside* can be the loss.
    An episode that ended anywhere else (an event that dealt lethal damage, or a
    step-limit truncation) leaves every fight it finished scored as a win.
    """
    segments: list[Combat] = []
    current: Combat | None = None
    current_key: tuple | None = None
    ended_fighting = False
    for row in rows:
        context = row.get("context") or {}
        kind = COMBAT_ROOMS.get(str(context.get("room_type") or ""))
        ended_fighting = False
        if not kind:
            current, current_key = None, None
            continue
        key = (context.get("act"), context.get("floor"))
        if current is None or current_key != key:
            boss = context.get("boss") or {}
            # A boss room is named from the map even before anything spawns, so a
            # boss encounter that spawns nothing still gets a row instead of
            # silently vanishing from the table.
            label = str(boss.get("name") or "") if kind == "boss" else ""
            current = Combat(kind=kind, label=label or "(no enemies)", enemies=(),
                             act=context.get("act"), floor=context.get("floor"),
                             result="win")
            current_key = key
            segments.append(current)
        enemies = row.get("enemies")
        if not enemies:
            continue
        if not current.enemies:
            label, names = _enemy_label(enemies)
            current.enemies = names
            if current.kind != "boss" or current.label == "(no enemies)":
                current.label = label
            current.hp_in = row.get("hp")
        current.hp_out = row.get("hp")
        ended_fighting = True
    # Only the fight the episode ends *inside* can be unfinished: it is the loss
    # if the run died there, and unresolved if the step limit cut it off. An
    # episode that ended in a shop or on an event has already left every fight it
    # entered, so all of them stand as wins whatever the run's own outcome was.
    if segments and ended_fighting:
        if outcome is False:
            segments[-1].result = "loss"
        elif outcome is None:
            segments[-1].result = "unresolved"
    return segments


def read_episode_combats(path: Path, outcome: bool | None) -> list[Combat]:
    import pyarrow.parquet as pq

    with PARQUET_LOCK:
        table = pq.read_table(path, columns=list(_COLUMNS), use_threads=False)
    return episode_combats(table.to_pylist(), outcome)


# --------------------------------------------------------------------------
# Per-run cache: one line per episode file, invalidated by (size, mtime).
# --------------------------------------------------------------------------

@dataclass
class RunCache:
    path: Path
    entries: dict[str, dict] = field(default_factory=dict)
    building: bool = False
    scanned: int = 0
    total: int = 0
    error: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    def load(self) -> None:
        if not self.path.exists():
            return
        for line in self.path.read_text(encoding="utf-8", errors="replace").splitlines():
            text = line.strip()
            if not text.startswith("{"):
                continue
            try:
                row = json.loads(text)
            except json.JSONDecodeError:
                continue
            if key := row.get("path"):
                self.entries[str(key)] = row

    def append(self, rows: list[dict]) -> None:
        if not rows:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")


_CACHES: dict[Path, RunCache] = {}
_CACHES_LOCK = threading.Lock()


def _cache_for(run_dir: Path) -> RunCache:
    key = run_dir.resolve()
    with _CACHES_LOCK:
        cache = _CACHES.get(key)
        if cache is None:
            cache = RunCache(path=key / CACHE_DIRNAME / CACHE_FILENAME)
            cache.load()
            _CACHES[key] = cache
        return cache


def _fingerprint(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_size, stat.st_mtime_ns


_MANIFEST_CACHE: dict[Path, tuple[tuple[int, int], list[dict]]] = {}


def _manifest_entries(run_dir: Path) -> list[dict]:
    """Parse the run manifest, memoised — a live run's manifest is ~1MB and the
    dashboard polls this endpoint, so re-parsing it per request is pure waste."""
    manifest = run_dir / "episodes" / "manifest.jsonl"
    fingerprint = _fingerprint(manifest)
    if fingerprint is None:
        return []
    cached = _MANIFEST_CACHE.get(manifest)
    if cached and cached[0] == fingerprint:
        return cached[1]
    rows: dict[str, dict] = {}
    for line in manifest.read_text(encoding="utf-8", errors="replace").splitlines():
        text = line.strip()
        if not text.startswith("{"):
            continue
        try:
            row = json.loads(text)
        except json.JSONDecodeError:
            continue
        if row.get("path") and not row.get("error"):
            # Re-recorded seeds reuse the same file; the last manifest line wins.
            rows[str(row["path"])] = row
    entries = list(rows.values())
    _MANIFEST_CACHE[manifest] = (fingerprint, entries)
    return entries


def _scan(run_dir: Path, cache: RunCache, pending: list[dict]) -> None:
    """Parse the episodes missing from the cache, newest iteration first."""
    written: list[dict] = []
    try:
        for entry in pending:
            relative = str(entry["path"])
            target = run_dir / relative
            fingerprint = _fingerprint(target)
            if fingerprint is None:
                continue
            try:
                combats = read_episode_combats(target, entry.get("outcome"))
            except Exception:  # a half-written or corrupt episode must not stop the pass
                combats = []
            row = {
                "path": relative, "size": fingerprint[0], "mtime": fingerprint[1],
                "iteration": entry.get("iteration"), "split": entry.get("split"),
                "stage": entry.get("stage"),
                "combats": [combat.as_dict() for combat in combats],
            }
            with cache.lock:
                cache.entries[relative] = row
                cache.scanned += 1
            written.append(row)
            if len(written) >= 200:
                cache.append(written)
                written = []
        cache.append(written)
    except Exception as exc:  # pragma: no cover - defensive: keep the thread quiet
        cache.error = str(exc)
    finally:
        cache.building = False


def refresh(run_dir: Path, *, blocking: bool = False) -> RunCache:
    """Bring the cache up to date with the manifest, in the background by default."""
    cache = _cache_for(run_dir)
    entries = _manifest_entries(run_dir)
    with cache.lock:
        pending = []
        for entry in entries:
            relative = str(entry["path"])
            cached = cache.entries.get(relative)
            fingerprint = _fingerprint(run_dir / relative)
            if fingerprint is None:
                continue
            if cached and (cached.get("size"), cached.get("mtime")) == fingerprint:
                continue
            pending.append(entry)
        cache.total = len(entries)
        cache.scanned = len(entries) - len(pending)
        if not pending:
            cache.building = False
            return cache
        if cache.building and not blocking:
            return cache  # a pass is already running; it or the next poll picks these up
        cache.building = True
    # Newest first: the tail of the run is what anyone reading a live dashboard
    # is actually looking at, so it becomes readable long before an old 6000-file
    # backlog finishes.
    pending.sort(key=lambda row: (row.get("iteration") or -1), reverse=True)
    if blocking:
        _scan(run_dir, cache, pending)
    else:
        threading.Thread(target=_scan, args=(run_dir, cache, pending),
                         name=f"encounters-{run_dir.name}", daemon=True).start()
    return cache


# --------------------------------------------------------------------------
# Aggregation
# --------------------------------------------------------------------------

@dataclass
class _Tally:
    fights: int = 0
    wins: int = 0
    losses: int = 0
    unresolved: int = 0
    hp_lost: float = 0.0
    hp_samples: int = 0
    hp_in: float = 0.0
    acts: set = field(default_factory=set)

    def add(self, combat: dict) -> None:
        self.fights += 1
        result = combat.get("result")
        self.wins += result == "win"
        self.losses += result == "loss"
        self.unresolved += result == "unresolved"
        if combat.get("act") is not None:
            self.acts.add(int(combat["act"]))
        hp_in, hp_out = combat.get("hp_in"), combat.get("hp_out")
        if isinstance(hp_in, (int, float)) and isinstance(hp_out, (int, float)):
            self.hp_lost += max(0.0, float(hp_in) - float(hp_out))
            self.hp_in += float(hp_in)
            self.hp_samples += 1

    def as_dict(self, key: str, label: str, kind: str) -> dict[str, Any]:
        resolved = self.wins + self.losses
        return {
            "key": key, "label": label, "kind": kind,
            "fights": self.fights, "wins": self.wins, "losses": self.losses,
            "unresolved": self.unresolved,
            "win_rate": round(self.wins / resolved, 4) if resolved else None,
            "avg_hp_lost": round(self.hp_lost / self.hp_samples, 1) if self.hp_samples else None,
            "avg_hp_in": round(self.hp_in / self.hp_samples, 1) if self.hp_samples else None,
            "acts": sorted(self.acts),
        }


def aggregate(cache: RunCache, *, split: str = "", last_iterations: int = 0) -> dict:
    """Roll cached per-episode fights up into encounter and per-enemy tables.

    ``last_iterations`` keeps only the most recent N *recorded* iterations, which
    is how you separate "the policy could never beat this" from "the policy could
    not beat this 2000 iterations ago".
    """
    with cache.lock:
        rows = [row for row in cache.entries.values()
                if not split or row.get("split") == split]
    iterations = sorted({int(row["iteration"]) for row in rows
                         if row.get("iteration") is not None})
    if last_iterations and len(iterations) > last_iterations:
        cutoff = iterations[-last_iterations]
        rows = [row for row in rows if (row.get("iteration") or 0) >= cutoff]
        iterations = [it for it in iterations if it >= cutoff]

    encounters: dict[tuple[str, str], _Tally] = {}
    enemies: dict[tuple[str, str], _Tally] = {}
    for row in rows:
        for combat in row.get("combats") or []:
            kind, label = str(combat.get("kind")), str(combat.get("label"))
            encounters.setdefault((kind, label), _Tally()).add(combat)
            # A monster that appears in several spawn groups is credited in each
            # of them; this is the "how dangerous is this creature" view.
            for name in combat.get("enemies") or []:
                enemies.setdefault((kind, str(name)), _Tally()).add(combat)

    def table(source: dict[tuple[str, str], _Tally]) -> list[dict]:
        out = [tally.as_dict(f"{kind}/{label}", label, kind)
               for (kind, label), tally in source.items()]
        out.sort(key=lambda item: (-item["fights"], item["label"]))
        return out

    grouped = table(encounters)
    return {
        "encounters": grouped,
        "enemies": table(enemies),
        "by_kind": {kind: [row for row in grouped if row["kind"] == kind] for kind in KINDS},
        "scope": {
            "split": split or "all", "episodes": len(rows),
            "iteration_from": iterations[0] if iterations else None,
            "iteration_to": iterations[-1] if iterations else None,
            "fights": sum(len(row.get("combats") or []) for row in rows),
        },
    }


def encounter_stats(run_dir: Path, *, split: str = "", last_iterations: int = 0,
                    blocking: bool = False) -> dict:
    """The dashboard endpoint payload: current numbers plus build progress."""
    cache = refresh(run_dir, blocking=blocking)
    payload = aggregate(cache, split=split, last_iterations=last_iterations)
    with cache.lock:
        scanned, total = cache.scanned, cache.total
    payload["progress"] = {
        "scanned": scanned, "total": total, "building": cache.building,
        "error": cache.error,
    }
    return payload


def _main(argv: list[str] | None = None) -> int:  # pragma: no cover - manual tool
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--split", default="")
    parser.add_argument("--last-iterations", type=int, default=0)
    args = parser.parse_args(argv)
    payload = encounter_stats(args.run_dir, split=args.split,
                              last_iterations=args.last_iterations, blocking=True)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
