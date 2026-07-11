from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import queue
import signal
import subprocess
import threading
from collections import deque
from typing import Any, Mapping, Sequence

from .protocol import ActionCandidate, DecisionState, StepResult, SUPPORTED_PROTOCOL_VERSION, parse_state


class EngineError(RuntimeError):
    pass


class EngineTimeout(EngineError):
    pass


class EngineFatal(EngineError):
    """The CLI poisoned its engine state and requires a fresh process."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.detail = message


def check_engine_response(response: Mapping[str, Any]) -> None:
    if response.get("type") == "error" and response.get("fatal") is True:
        raise EngineFatal(
            str(response.get("code", "engine_fatal")),
            str(response.get("message", "engine process is unusable")),
        )


@dataclass(frozen=True)
class RunConfig:
    character: str
    seed: str
    ascension: int = 0
    lang: str = "en"


class EngineClient:
    """One persistent sts2-cli process with timeout detection and restart."""

    def __init__(self, command: Sequence[str], *, cwd: Path, timeout: float = 10.0, env: Mapping[str, str] | None = None):
        self.command = tuple(command)
        self.cwd = Path(cwd)
        self.timeout = timeout
        self.env = {**os.environ, **(env or {})}
        self._proc: subprocess.Popen[str] | None = None
        self._lines: queue.Queue[str | BaseException] = queue.Queue()
        self.version: str | None = None
        self.trace: list[dict[str, Any]] = []
        self.stderr_tail: deque[str] = deque(maxlen=80)

    def _start(self) -> None:
        self.close()
        self._lines = queue.Queue()
        popen_options: dict[str, Any] = {}
        if os.name == "posix":
            # Own the whole process group. Commands such as ``dotnet run`` spawn
            # the app as a child; killing only the wrapper leaked Sts2Headless
            # processes after timeouts and caused cascading evaluator failures.
            popen_options["start_new_session"] = True
        elif os.name == "nt":
            popen_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        self._proc = subprocess.Popen(
            self.command,
            cwd=self.cwd,
            env=self.env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            **popen_options,
        )
        assert self._proc.stdout is not None
        stream = self._proc.stdout
        err_stream = self._proc.stderr

        def reader() -> None:
            try:
                for line in stream:
                    if line.strip().startswith("{"):
                        self._lines.put(line)
            except BaseException as exc:
                self._lines.put(exc)

        threading.Thread(target=reader, daemon=True).start()
        def error_reader() -> None:
            assert err_stream is not None
            for line in err_stream:
                self.stderr_tail.append(line.rstrip())
        threading.Thread(target=error_reader, daemon=True).start()
        ready = self._read()
        if ready.get("type") != "ready":
            raise EngineError(f"expected ready handshake, got {ready!r}")
        self.version = str(ready.get("version", ""))
        if self.version != SUPPORTED_PROTOCOL_VERSION:
            raise EngineError(f"protocol version {self.version!r} != {SUPPORTED_PROTOCOL_VERSION!r}")

    def _read(self) -> dict[str, Any]:
        try:
            item = self._lines.get(timeout=self.timeout)
        except queue.Empty as exc:
            recent = self.trace[-5:]
            self._kill()
            raise EngineTimeout(f"no JSON response within {self.timeout}s; recent_trace={recent!r}; stderr_tail={list(self.stderr_tail)[-12:]!r}") from exc
        if isinstance(item, BaseException):
            raise EngineError("engine stdout reader failed") from item
        try:
            return json.loads(item)
        except json.JSONDecodeError as exc:
            raise EngineError(f"invalid JSON response: {item!r}") from exc

    def _request(self, command: Mapping[str, Any]) -> dict[str, Any]:
        if self._proc is None or self._proc.poll() is not None:
            self._start()
        assert self._proc and self._proc.stdin
        try:
            self._proc.stdin.write(json.dumps(command, separators=(",", ":")) + "\n")
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            self._kill()
            raise EngineError("engine process terminated while writing") from exc
        response = self._read()
        try:
            check_engine_response(response)
        except EngineFatal as exc:
            detail = (
                f"{exc.detail}; recent_trace={self.trace[-5:]!r}; "
                f"stderr_tail={list(self.stderr_tail)[-20:]!r}"
            )
            self._kill()
            raise EngineFatal(exc.code, detail) from exc
        return response

    def reset(self, config: RunConfig) -> DecisionState:
        # start_run is expected to fully replace the previous run; M0 must verify this upstream.
        command = {"cmd": "start_run", "character": config.character, "seed": config.seed, "ascension": config.ascension, "lang": config.lang}
        self.trace = [command]
        raw = self._request(command)
        return parse_state(raw)

    def step(self, action: ActionCandidate) -> StepResult:
        try:
            command = action.command()
            self.trace.append(command)
            state = parse_state(self._request(command))
            return StepResult(state, state.phase == "game_over")
        except EngineError:
            self._kill()
            raise

    def _kill(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            proc = self._proc
            try:
                if os.name == "posix":
                    os.killpg(proc.pid, signal.SIGKILL)
                elif os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=False,
                    )
                else:
                    proc.kill()
                proc.wait(timeout=5)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                pass
        self._proc = None

    def close(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            try:
                assert self._proc.stdin
                self._proc.stdin.write('{"cmd":"quit"}\n')
                self._proc.stdin.flush()
                self._proc.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                self._kill()
        self._proc = None

    def __enter__(self) -> "EngineClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
