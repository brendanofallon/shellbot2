"""
Tests for the interactive chat REPL feature (cli chat subcommand).

Tests cover:
1. Slash command routing logic (pure unit tests, no I/O)
2. Parser recognises the 'chat' subcommand and --new-thread flag
3. _get_input_gum falls back gracefully when gum is absent
4. run_chat terminates cleanly on /quit and on EOFError
5. run_chat starts a new thread on /new
6. run_chat correctly lists threads via /threads
"""

import argparse
import asyncio
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_args(tmp_path: Path, new_thread: bool = False) -> argparse.Namespace:
    """Build a minimal argparse Namespace that run_chat expects."""
    return SimpleNamespace(datadir=tmp_path, new_thread=new_thread)


def _write_conf(tmp_path: Path) -> None:
    """Write a minimal agent_conf.yaml so ShellBot3 can be constructed."""
    conf = {
        "provider": "gemini",
        "model": "gemini-3-flash-preview",
        "instructions": "test",
        "input_address": "tcp://127.0.0.1:5555",
        "tools": [],   # no tools → nothing to init
    }
    (tmp_path / "agent_conf.yaml").write_text(yaml.dump(conf))


# ---------------------------------------------------------------------------
# 1. Parser recognises 'chat' and '--new-thread'
# ---------------------------------------------------------------------------

class TestChatParser:
    def test_chat_subcommand_registered(self):
        from shellbot2.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["chat"])
        assert args.command == "chat"
        assert args.new_thread is False

    def test_chat_new_thread_flag(self):
        from shellbot2.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["chat", "--new-thread"])
        assert args.command == "chat"
        assert args.new_thread is True


# ---------------------------------------------------------------------------
# 2. _get_input_gum falls back when gum is absent
# ---------------------------------------------------------------------------

class TestGetInputGum:
    def test_returns_none_when_gum_missing(self):
        from shellbot2.cli import _get_input_gum
        with patch("shutil.which", return_value=None):
            result = _get_input_gum("🤖 > ")
        assert result is None

    def test_returns_string_when_gum_present(self):
        from shellbot2.cli import _get_input_gum
        import subprocess
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "hello world\n"
        with patch("shutil.which", return_value="/usr/bin/gum"),              patch("subprocess.run", return_value=mock_result):
            result = _get_input_gum("🤖 > ")
        assert result == "hello world"

    def test_returns_empty_on_gum_cancel(self):
        """gum exits with non-zero when user presses Escape/Ctrl-C."""
        from shellbot2.cli import _get_input_gum
        mock_result = MagicMock()
        mock_result.returncode = 130
        mock_result.stdout = ""
        with patch("shutil.which", return_value="/usr/bin/gum"),              patch("subprocess.run", return_value=mock_result):
            result = _get_input_gum("🤖 > ")
        assert result == ""


# ---------------------------------------------------------------------------
# 3. run_chat terminates on /quit
# ---------------------------------------------------------------------------

class TestRunChatQuit:
    @pytest.mark.asyncio
    async def test_quit_command_exits(self, tmp_path):
        _write_conf(tmp_path)
        args = _make_args(tmp_path)

        # Input sequence: /quit
        inputs = iter(["/quit"])

        mock_agent = MagicMock()
        mock_agent.thread_id = str(uuid.uuid4())
        mock_agent.run = AsyncMock()

        with patch("shellbot2.cli.ShellBot3", return_value=mock_agent),              patch("shellbot2.cli.create_rich_output_dispatcher", return_value=MagicMock()),              patch("shellbot2.cli._get_input_gum", return_value=None),              patch("builtins.input", side_effect=inputs):
            from shellbot2.cli import run_chat
            await run_chat(args)

        # Agent should never have been asked to run a prompt
        mock_agent.run.assert_not_called()

    @pytest.mark.asyncio
    async def test_exit_command_exits(self, tmp_path):
        _write_conf(tmp_path)
        args = _make_args(tmp_path)

        inputs = iter(["/exit"])
        mock_agent = MagicMock()
        mock_agent.thread_id = str(uuid.uuid4())
        mock_agent.run = AsyncMock()

        with patch("shellbot2.cli.ShellBot3", return_value=mock_agent),              patch("shellbot2.cli.create_rich_output_dispatcher", return_value=MagicMock()),              patch("shellbot2.cli._get_input_gum", return_value=None),              patch("builtins.input", side_effect=inputs):
            from shellbot2.cli import run_chat
            await run_chat(args)

        mock_agent.run.assert_not_called()

    @pytest.mark.asyncio
    async def test_eoferror_exits_gracefully(self, tmp_path):
        _write_conf(tmp_path)
        args = _make_args(tmp_path)

        mock_agent = MagicMock()
        mock_agent.thread_id = str(uuid.uuid4())
        mock_agent.run = AsyncMock()

        with patch("shellbot2.cli.ShellBot3", return_value=mock_agent),              patch("shellbot2.cli.create_rich_output_dispatcher", return_value=MagicMock()),              patch("shellbot2.cli._get_input_gum", return_value=None),              patch("builtins.input", side_effect=EOFError):
            from shellbot2.cli import run_chat
            # Should not raise
            await run_chat(args)

        mock_agent.run.assert_not_called()


# ---------------------------------------------------------------------------
# 4. run_chat dispatches normal prompts to the agent
# ---------------------------------------------------------------------------

class TestRunChatPromptDispatch:
    @pytest.mark.asyncio
    async def test_sends_prompt_to_agent(self, tmp_path):
        _write_conf(tmp_path)
        args = _make_args(tmp_path)

        inputs = iter(["tell me a joke", "/quit"])
        mock_agent = MagicMock()
        mock_agent.thread_id = str(uuid.uuid4())
        mock_agent.run = AsyncMock(return_value=None)

        with patch("shellbot2.cli.ShellBot3", return_value=mock_agent),              patch("shellbot2.cli.create_rich_output_dispatcher", return_value=MagicMock()),              patch("shellbot2.cli._get_input_gum", return_value=None),              patch("builtins.input", side_effect=inputs):
            from shellbot2.cli import run_chat
            await run_chat(args)

        mock_agent.run.assert_called_once_with("tell me a joke")

    @pytest.mark.asyncio
    async def test_multiple_prompts(self, tmp_path):
        _write_conf(tmp_path)
        args = _make_args(tmp_path)

        inputs = iter(["first", "second", "/quit"])
        mock_agent = MagicMock()
        mock_agent.thread_id = str(uuid.uuid4())
        mock_agent.run = AsyncMock(return_value=None)

        with patch("shellbot2.cli.ShellBot3", return_value=mock_agent),              patch("shellbot2.cli.create_rich_output_dispatcher", return_value=MagicMock()),              patch("shellbot2.cli._get_input_gum", return_value=None),              patch("builtins.input", side_effect=inputs):
            from shellbot2.cli import run_chat
            await run_chat(args)

        assert mock_agent.run.call_count == 2
        mock_agent.run.assert_any_call("first")
        mock_agent.run.assert_any_call("second")

    @pytest.mark.asyncio
    async def test_blank_lines_skipped(self, tmp_path):
        _write_conf(tmp_path)
        args = _make_args(tmp_path)

        inputs = iter(["", "   ", "hello", "/quit"])
        mock_agent = MagicMock()
        mock_agent.thread_id = str(uuid.uuid4())
        mock_agent.run = AsyncMock(return_value=None)

        with patch("shellbot2.cli.ShellBot3", return_value=mock_agent),              patch("shellbot2.cli.create_rich_output_dispatcher", return_value=MagicMock()),              patch("shellbot2.cli._get_input_gum", return_value=None),              patch("builtins.input", side_effect=inputs):
            from shellbot2.cli import run_chat
            await run_chat(args)

        mock_agent.run.assert_called_once_with("hello")


# ---------------------------------------------------------------------------
# 5. /new creates a fresh thread
# ---------------------------------------------------------------------------

class TestRunChatNewThread:
    @pytest.mark.asyncio
    async def test_new_command_creates_new_agent(self, tmp_path):
        _write_conf(tmp_path)
        args = _make_args(tmp_path)

        tid1 = str(uuid.uuid4())
        tid2 = str(uuid.uuid4())

        agent1 = MagicMock()
        agent1.thread_id = tid1
        agent1.run = AsyncMock(return_value=None)

        agent2 = MagicMock()
        agent2.thread_id = tid2
        agent2.run = AsyncMock(return_value=None)

        agents = iter([agent1, agent2])

        inputs = iter(["/new", "/quit"])

        with patch("shellbot2.cli.ShellBot3", side_effect=agents),              patch("shellbot2.cli.create_rich_output_dispatcher", return_value=MagicMock()),              patch("shellbot2.cli._get_input_gum", return_value=None),              patch("builtins.input", side_effect=inputs):
            from shellbot2.cli import run_chat
            await run_chat(args)

        # ShellBot3 should have been constructed twice: once on startup, once on /new
        # (the side_effect iterator consumed both agent1 and agent2)
        assert agent1.run.call_count == 0
        assert agent2.run.call_count == 0


# ---------------------------------------------------------------------------
# 6. /threads lists thread IDs
# ---------------------------------------------------------------------------

class TestRunChatThreads:
    @pytest.mark.asyncio
    async def test_threads_command_lists_ids(self, tmp_path):
        _write_conf(tmp_path)
        args = _make_args(tmp_path)

        mock_agent = MagicMock()
        mock_agent.thread_id = "thread-abc"
        mock_agent.run = AsyncMock(return_value=None)

        mock_mh = MagicMock()
        mock_mh.get_thread_ids.return_value = ["thread-abc", "thread-xyz"]

        inputs = iter(["/threads", "/quit"])
        printed_lines = []

        mock_console = MagicMock()
        mock_console.print.side_effect = lambda *a, **kw: printed_lines.append(str(a))

        with patch("shellbot2.cli.ShellBot3", return_value=mock_agent),              patch("shellbot2.cli.create_rich_output_dispatcher", return_value=MagicMock()),              patch("shellbot2.cli.MessageHistory", return_value=mock_mh),              patch("shellbot2.cli._get_input_gum", return_value=None),              patch("rich.console.Console", return_value=mock_console),              patch("builtins.input", side_effect=inputs):
            from shellbot2 import cli
            import importlib
            importlib.reload(cli)
            await cli.run_chat(args)

        mock_mh.get_thread_ids.assert_called_once()
