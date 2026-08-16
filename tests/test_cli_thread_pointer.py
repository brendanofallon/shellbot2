import argparse
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from shellbot2.cli import daemon_ask, resolve_cli_thread_id, run_prompt
from shellbot2.database import database_path
from shellbot2.message_history import MessageHistory


def test_cli_thread_pointer_is_durable_and_ignores_configured_sensor_threads(tmp_path):
    (tmp_path / "agent_conf.yaml").write_text(
        """
sensors:
  enabled: true
  entries:
    - name: disk_usage
      thread_id: background-disk-alerts
""".strip()
        + "\n"
    )
    history = MessageHistory(database_path(tmp_path))
    history.add_message("interactive-thread", {"prompt": "hello"})
    history.add_message("background-disk-alerts", {"prompt": "disk nearly full"})

    thread_id = resolve_cli_thread_id(tmp_path, new_thread=False)

    assert thread_id == "interactive-thread"
    assert history.get_active_thread_id("cli") == "interactive-thread"

    history.add_message("background-disk-alerts", {"prompt": "disk full"})
    assert resolve_cli_thread_id(tmp_path, new_thread=False) == "interactive-thread"


def test_new_cli_thread_replaces_the_durable_pointer(tmp_path):
    history = MessageHistory(database_path(tmp_path))
    history.set_active_thread_id("cli", "old-thread")

    thread_id = resolve_cli_thread_id(tmp_path, new_thread=True)

    assert thread_id != "old-thread"
    assert resolve_cli_thread_id(tmp_path, new_thread=False) == thread_id


def test_direct_cli_prompt_uses_the_durable_thread_pointer(tmp_path):
    history = MessageHistory(database_path(tmp_path))
    history.set_active_thread_id("cli", "interactive-thread")
    agent = MagicMock()
    agent.run = AsyncMock()
    args = argparse.Namespace(datadir=tmp_path, prompt="hello", new_thread=False)

    with (
        patch("shellbot2.cli.ShellBot3", return_value=agent) as shellbot,
        patch("shellbot2.cli.create_rich_output_dispatcher", return_value=MagicMock()),
    ):
        asyncio.run(run_prompt(args))

    assert shellbot.call_args.kwargs["thread_id"] == "interactive-thread"
    agent.run.assert_awaited_once()


def test_daemon_cli_prompt_includes_the_durable_thread_pointer(tmp_path):
    history = MessageHistory(database_path(tmp_path))
    history.set_active_thread_id("cli", "interactive-thread")
    output_socket = MagicMock()
    input_socket = MagicMock()
    context = MagicMock()
    context.socket.side_effect = [output_socket, input_socket]
    handler = MagicMock()
    args = argparse.Namespace(datadir=tmp_path, prompt="hello", new_thread=False)

    with (
        patch("shellbot2.cli.daemon_is_running", return_value=True),
        patch(
            "shellbot2.cli.load_conf",
            return_value={
                "input_address": "tcp://127.0.0.1:15555",
                "output_address": "tcp://127.0.0.1:15556",
            },
        ),
        patch("shellbot2.cli.zmq.Context", return_value=context),
        patch("shellbot2.cli.time.sleep"),
        patch("shellbot2.cli.RichOutputHandler", return_value=handler),
        patch(
            "shellbot2.cli.BaseEvent.model_validate_json",
            return_value=SimpleNamespace(type="RUN_FINISHED"),
        ),
    ):
        asyncio.run(daemon_ask(args))

    message = input_socket.send_json.call_args.args[0]
    assert message["thread_id"] == "interactive-thread"
