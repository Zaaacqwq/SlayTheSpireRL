import pytest
import sys

from sts2rl.engine import EngineClient, EngineFatal, RunConfig, check_engine_response


def test_trace_is_available_before_timeout():
    client = EngineClient(["does-not-run"], cwd=".")
    client.trace.append({"cmd": "action", "action": "end_turn"})
    assert client.trace[-1]["action"] == "end_turn"


def test_fatal_engine_response_is_not_a_decision_state():
    with pytest.raises(EngineFatal) as error:
        check_engine_response({
            "type": "error",
            "fatal": True,
            "code": "quiescence_timeout",
            "message": "engine did not settle",
        })
    assert error.value.code == "quiescence_timeout"


def test_fatal_response_kills_process_and_next_reset_restarts(tmp_path):
    fake_cli = r'''
import json, sys
print(json.dumps({"type":"ready","version":"0.2.0"}), flush=True)
for line in sys.stdin:
    command = json.loads(line)
    if command["cmd"] == "start_run":
        print(json.dumps({"type":"decision","decision":"event_choice","options":[{"index":0}]}), flush=True)
    else:
        print(json.dumps({"type":"error","fatal":True,"code":"quiescence_timeout","message":"poisoned"}), flush=True)
        break
'''
    client = EngineClient([sys.executable, "-u", "-c", fake_cli], cwd=tmp_path)
    state = client.reset(RunConfig("Ironclad", "first"))
    with pytest.raises(EngineFatal):
        client.step(state.candidates[0])
    assert client._proc is None
    restarted = client.reset(RunConfig("Ironclad", "second"))
    assert restarted.phase == "event_choice"
    client.close()
