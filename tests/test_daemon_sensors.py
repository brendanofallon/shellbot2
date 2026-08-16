import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

from shellbot2.daemon import AgentDaemon, InputMessage


def _write_conf(tmp_path, extra: str = "") -> None:
    (tmp_path / "agent_conf.yaml").write_text(
        f"""
provider: gemini
model: test-model
input_address: tcp://127.0.0.1:15555
output_address: tcp://127.0.0.1:15556
instructions: test
tools: []
{extra}
""".strip()
        + "\n"
    )


def _patch_daemon_deps(monkeypatch):
    agent = MagicMock()
    agent.run = AsyncMock()
    agent.thread_id = "default-thread"
    monkeypatch.setattr("shellbot2.daemon.ShellBot3", lambda *args, **kwargs: agent)
    context = MagicMock()
    context.socket.return_value = MagicMock()
    monkeypatch.setattr("shellbot2.daemon.zmq.Context", lambda: context)
    return agent


def test_input_message_from_json_is_backward_compatible():
    message = InputMessage.from_json(
        json.dumps(
            {
                "prompt": "hello",
                "source": "cli",
                "datetime": "2026-01-01T00:00:00",
            }
        )
    )
    assert message.prompt == "hello"
    assert message.source == "cli"
    assert message.thread_id is None
    assert message.event_id is None
    assert message.metadata == {}


def test_input_message_rejects_non_json_metadata():
    try:
        InputMessage.from_json(
            json.dumps(
                {
                    "prompt": "hello",
                    "source": "cli",
                    "datetime": "2026-01-01T00:00:00",
                    "metadata": ["not", "an", "object"],
                }
            )
        )
    except ValueError as exc:
        assert "metadata" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_queue_executes_fifo_and_never_concurrent(tmp_path, monkeypatch):
    _write_conf(tmp_path)
    agent = _patch_daemon_deps(monkeypatch)

    async def body():
        order: list[str] = []
        in_run = 0
        max_in_run = 0
        entered = asyncio.Event()
        release = asyncio.Event()

        async def fake_run(prompt: str) -> None:
            nonlocal in_run, max_in_run
            in_run += 1
            max_in_run = max(max_in_run, in_run)
            order.append(prompt)
            entered.set()
            await release.wait()
            in_run -= 1

        agent.run.side_effect = fake_run
        daemon = AgentDaemon(tmp_path)
        try:
            await daemon._start_processing()
            assert daemon._scheduler is None
            await daemon._input_queue.put(
                InputMessage(prompt="first", source="cli", datetime="t0")
            )
            await daemon._input_queue.put(
                InputMessage(prompt="second", source="cli", datetime="t1")
            )
            await asyncio.wait_for(entered.wait(), timeout=2)
            for _ in range(20):
                await asyncio.sleep(0)
            assert order == ["first"]
            assert max_in_run == 1
            release.set()
            for _ in range(50):
                if order == ["first", "second"]:
                    break
                await asyncio.sleep(0)
            assert order == ["first", "second"]
            assert max_in_run == 1
        finally:
            await daemon.stop()

    asyncio.run(body())


def test_malformed_external_message_is_dropped(tmp_path, monkeypatch):
    _write_conf(tmp_path)
    agent = _patch_daemon_deps(monkeypatch)

    async def body():
        daemon = AgentDaemon(tmp_path)
        try:
            await daemon._start_processing()
            daemon._input_socket = MagicMock()
            daemon._running = True

            async def recv_then_stop():
                daemon._running = False
                return b"{not-json"

            daemon._input_socket.recv = recv_then_stop
            await daemon._receive_loop()
            assert agent.run.await_count == 0
        finally:
            daemon._running = False
            await daemon.stop()

    asyncio.run(body())


FAKE_PLUGIN = """
from shellbot2.sensorframework.sensor_spec import SensorObservation, SensorSpec

class FakeSensor:
    async def poll(self, runtime):
        return [
            SensorObservation(
                kind="ping",
                summary="sensor ping",
                dedupe_key="ping-1",
                payload={"n": 1},
            )
        ]

SENSOR_SPECS = (
    SensorSpec(
        name="fake_sensor",
        description="Test-only sensor",
        factory=lambda runtime: FakeSensor(),
        default_interval_seconds=300,
    ),
)
"""


def test_daemon_without_sensors_config_does_not_create_sensor_store(tmp_path, monkeypatch):
    _write_conf(tmp_path)
    _patch_daemon_deps(monkeypatch)
    daemon = AgentDaemon(tmp_path)
    assert daemon._scheduler is None
    assert daemon._state_store is None
    assert not (tmp_path / "shellbot2.db").exists()
    asyncio.run(daemon.stop())


def test_daemon_sensor_event_uses_untrusted_prompt_and_serializes(tmp_path, monkeypatch):
    sensors_dir = tmp_path / "sensors"
    sensors_dir.mkdir()
    (sensors_dir / "fake.py").write_text(FAKE_PLUGIN.strip())
    _write_conf(
        tmp_path,
        extra="""
sensors:
  enabled: true
  queue_maxsize: 10
  entries:
    - name: fake_sensor
      enabled: true
      cooldown_seconds: 900
""",
    )
    agent = _patch_daemon_deps(monkeypatch)
    prompts: list[str] = []
    in_run = 0
    max_in_run = 0
    done = asyncio.Event()

    async def fake_run(prompt: str) -> None:
        nonlocal in_run, max_in_run
        in_run += 1
        max_in_run = max(max_in_run, in_run)
        prompts.append(prompt)
        await asyncio.sleep(0)
        in_run -= 1
        if len(prompts) >= 2:
            done.set()

    agent.run.side_effect = fake_run

    async def body():
        daemon = AgentDaemon(tmp_path)
        try:
            assert daemon._scheduler is not None
            await daemon._start_processing()
            await daemon._input_queue.put(
                InputMessage(prompt="user hello", source="cli", datetime="t0", thread_id="user-thread")
            )
            await asyncio.wait_for(done.wait(), timeout=2)
            assert max_in_run == 1
            assert any("user hello" in prompt for prompt in prompts)
            sensor_prompt = next(prompt for prompt in prompts if "UNTRUSTED SENSOR OBSERVATION" in prompt)
            assert "untrusted external data" in sensor_prompt
            assert "sensor ping" in sensor_prompt
            assert agent.thread_id in {"user-thread", "sensor:fake_sensor"}
        finally:
            await daemon.stop()
            await daemon.stop()

    asyncio.run(body())
    assert (tmp_path / "shellbot2.db").exists()


def test_enqueue_sensor_event_returns_false_when_queue_full(tmp_path, monkeypatch):
    _write_conf(
        tmp_path,
        extra="""
sensors:
  enabled: true
  queue_maxsize: 1
  entries: []
""",
    )
    _patch_daemon_deps(monkeypatch)

    async def body():
        daemon = AgentDaemon(tmp_path)
        try:
            daemon._input_queue = asyncio.Queue(maxsize=1)
            daemon._input_queue.put_nowait(
                InputMessage(prompt=" occupying", source="cli", datetime="t0")
            )
            message = InputMessage(
                prompt="sensor",
                source="sensor:x",
                datetime="t1",
                event_id="e1",
                metadata={"sensor_name": "x"},
            )
            assert daemon.enqueue_sensor_event(message) is False
        finally:
            await daemon.stop()

    asyncio.run(body())
