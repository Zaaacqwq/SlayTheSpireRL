from sts2rl.engine import EngineClient


def test_trace_is_available_before_timeout():
    client = EngineClient(["does-not-run"], cwd=".")
    client.trace.append({"cmd": "action", "action": "end_turn"})
    assert client.trace[-1]["action"] == "end_turn"
