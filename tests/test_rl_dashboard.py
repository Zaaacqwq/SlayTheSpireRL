from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import threading
from pathlib import Path
from urllib.request import urlopen

import pytest

from tools.rl_dashboard import (
    DashboardHandler,
    DashboardServer,
    api_episode_detail,
    build_catalog,
    load_eval_summary,
    load_history_for_run,
    list_episodes,
    run_metrics,
    run_summary,
    summarize_episode,
)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")


def make_episode_rows() -> list[dict]:
    return [
        {
            "episode_id": "sample-episode",
            "step": 0,
            "state": {
                "decision": "event_choice",
                "context": {"act": 1, "floor": 2, "room_type": "Event", "act_name": "Act 1"},
                "player": {"name": "Ironclad", "hp": 70, "max_hp": 80, "block": 5, "gold": 99, "deck": ["Strike"], "relics": ["Burning Blood"], "potions": ["Potion"], "deck_size": 1},
                "choices": [{"col": 0, "row": 1, "type": "Event"}],
                "options": [{"index": 0, "title": "Proceed"}],
                "enemies": [],
                "hand": [{"index": 0, "name": "Strike"}],
                "cards": [{"index": 0, "name": "Strike"}],
            },
            "normalized": {"phase": "event_choice", "global": [1.0]},
            "legal_actions": [{"cmd": "choose_option", "action": "choose_option", "args": {"option_index": 0}}],
            "action": {"cmd": "choose_option", "action": "choose_option", "args": {"option_index": 0}},
            "reward": 0.0,
            "terminated": False,
            "outcome": None,
            "engine_version": "0.2.0",
        },
        {
            "episode_id": "sample-episode",
            "step": 1,
            "state": {
                "decision": "game_over",
                "context": {"act": 1, "floor": 3, "room_type": "Boss", "act_name": "Act 1"},
                "player": {"name": "Ironclad", "hp": 0, "max_hp": 80, "block": 0, "gold": 120, "deck": ["Strike", "Defend"], "relics": ["Burning Blood"], "potions": [], "deck_size": 2},
                "choices": [],
                "options": [],
                "enemies": [],
                "hand": [],
                "cards": [],
            },
            "normalized": {"phase": "game_over", "global": [0.0]},
            "legal_actions": [],
            "action": {"cmd": "none", "action": "none", "args": {}},
            "reward": -1.0,
            "terminated": True,
            "outcome": "false",
            "engine_version": "0.2.0",
        },
    ]


def make_parquet(path: Path, rows: list[dict]) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), path)


def test_history_loader_prefers_jsonl_over_log(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    run_dir = root / "m2_demo"
    run_dir.mkdir(parents=True)
    (run_dir / "config.json").write_text("{\"run_name\":\"m2_demo\"}\n", encoding="utf-8")
    write_jsonl(run_dir / "history.jsonl", [{"iteration": 1, "train_win_rate": 0.5, "loss": 1.0}])
    (root / "m2_demo.log").write_text("noise\n{\"iteration\": 2, \"train_win_rate\": 0.7, \"loss\": 0.8}\n", encoding="utf-8")

    rows, source = load_history_for_run(root, run_dir)
    assert source.endswith("history.jsonl")
    assert rows[0]["iteration"] == 1


def test_history_loader_reads_json_lines_from_log(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    run_dir = root / "m2_demo"
    run_dir.mkdir(parents=True)
    (run_dir / "config.json").write_text("{\"run_name\":\"m2_demo\"}\n", encoding="utf-8")
    (root / "m2_demo.log").write_text(
        "warning\n{\"iteration\": 1, \"train_win_rate\": 0.25, \"loss\": 1.2, \"stage\": \"normal\"}\nnot-json\n{\"iteration\": 2, \"train_win_rate\": 0.75, \"loss\": 0.9, \"stage\": \"mixed\"}\n",
        encoding="utf-8",
    )

    rows, source = load_history_for_run(root, run_dir)
    assert source.endswith("m2_demo.log")
    assert [row["iteration"] for row in rows] == [1, 2]


def test_history_loader_merges_dev_metrics_by_iteration(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    run_dir = root / "m2_demo"
    run_dir.mkdir(parents=True)
    (run_dir / "config.json").write_text("{}\n")
    (root / "m2_demo.log").write_text(
        '{"iteration":9,"stage":"act1","train_win_rate":0.25,"avg_floor":7,"loss":0.8}\n'
        '{"iteration":9,"stage":"act1","dev":{"win_rate":0.4,"avg_floor":9,"errors":0}}\n'
    )
    rows, _ = load_history_for_run(root, run_dir)
    assert rows == [{
        "iteration": 9, "stage": "act1", "train_win_rate": 0.25,
        "avg_floor": 7, "loss": 0.8, "dev_win_rate": 0.4,
        "dev_avg_floor": 9, "dev_errors": 0,
    }]


def test_eval_loader_supports_dict_and_jsonl(tmp_path: Path) -> None:
    dict_path = tmp_path / "eval.json"
    dict_path.write_text(json.dumps({"policy": "heuristic", "win_rate": 0.25, "errors": 2}) + "\n", encoding="utf-8")
    jsonl_path = tmp_path / "eval.jsonl"
    write_jsonl(jsonl_path, [{"outcome": True}, {"outcome": False}, {"error": "boom"}])

    dict_summary = load_eval_summary(dict_path)
    jsonl_summary = load_eval_summary(jsonl_path)
    assert dict_summary["policy"] == "heuristic"
    assert dict_summary["win_rate"] == 0.25
    assert jsonl_summary["rows"] == 3
    assert jsonl_summary["wins"] == 1


def test_episode_summary_and_detail_round_trip(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    episode_path = root / "m1_trajectories" / "sample.parquet"
    make_parquet(episode_path, make_episode_rows())

    summary = summarize_episode(episode_path)
    assert summary["rows"] == 2
    assert summary["character"] == "Ironclad"
    assert summary["last_room"] == "Boss"

    detail = api_episode_detail(root, "m1_trajectories/sample.parquet")
    assert detail["episode"]["episode_id"] == "sample-episode"
    assert detail["rows"][0]["decision"] == "event_choice"
    assert detail["rows"][1]["outcome"] == "false"


def test_catalog_and_http_smoke(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    run_dir = root / "m2_demo"
    run_dir.mkdir(parents=True)
    (run_dir / "config.json").write_text(json.dumps({"run_name": "m2_demo", "workers": 2}) + "\n", encoding="utf-8")
    write_jsonl(run_dir / "history.jsonl", [{"iteration": 1, "train_win_rate": 0.5, "loss": 1.0}])
    write_jsonl(root / "m2_demo.jsonl", [{"character": "Ironclad", "outcome": True}])
    make_parquet(root / "m1_trajectories" / "sample.parquet", make_episode_rows())

    catalog = build_catalog(root)
    assert catalog["runs"][0]["name"] == "m2_demo"
    assert catalog["episodes"][0]["episode_id"] == "sample-episode"

    server = DashboardServer(("127.0.0.1", 0), DashboardHandler, root)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        with urlopen(f"http://{host}:{port}/api/catalog") as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert payload["runs"][0]["name"] == "m2_demo"
        with urlopen(f"http://{host}:{port}/api/episodes/{'m1_trajectories/sample.parquet'}") as response:
            episode = json.loads(response.read().decode("utf-8"))
        assert episode["episode"]["episode_id"] == "sample-episode"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_live_run_api_reads_manifest_and_pivots_scalars(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    run = root / "live_run"
    run.mkdir(parents=True)
    (run / "config.json").write_text('{"run_name":"live_run"}\n')
    write_jsonl(run / "tb" / "scalars.jsonl", [
        {"name": "act1/train_win_rate", "value": 0.25, "step": 3},
        {"name": "act1/loss", "value": 0.8, "step": 3},
    ])
    write_jsonl(run / "episodes" / "manifest.jsonl", [{
        "episode_id": "seed-1", "path": "episodes/train/seed-1.parquet",
        "iteration": 3, "stage": "act1", "split": "train", "outcome": True,
        "total_reward": 1.4, "final_floor": 12, "steps": 50,
        "truncated": False, "error": None,
    }])

    metrics = run_metrics(root, "live_run")
    summary = run_summary(root, run)
    episodes = list_episodes(root, "live_run", {"outcome": ["win"]})
    assert metrics["rows"][0]["train_win_rate"] == 0.25
    assert summary["stats"]["win_rate"] == 1.0
    assert summary["stats"]["avg_reward"] == 1.4
    assert episodes["total"] == 1


def test_concurrent_legacy_requests_do_not_overlap_parquet_reads(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    make_parquet(root / "m1_trajectories" / "sample.parquet", make_episode_rows())
    server = DashboardServer(("127.0.0.1", 0), DashboardHandler, root)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    urls = [
        f"http://{host}:{port}/api/runs",
        f"http://{host}:{port}/api/runs/legacy/episodes?page_size=20",
        f"http://{host}:{port}/api/runs/legacy/episodes/sample-episode",
    ]

    def fetch(index: int) -> dict:
        with urlopen(urls[index % len(urls)], timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    try:
        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(fetch, range(60)))
        assert len(results) == 60
        assert any(payload.get("legacy_episode_count") == 1 for payload in results)
        assert any(payload.get("rows") for payload in results)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
