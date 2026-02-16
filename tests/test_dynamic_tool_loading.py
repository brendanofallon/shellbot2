"""
Tests for the dynamic tool loading feature.

These tests verify that:
1. The TOOL_REGISTRY contains all expected tools.
2. _create_tools() loads only tools listed in config.
3. _create_tools() loads all tools when config has no tools list.
4. _create_tools() handles tools with sub-config (e.g. document-store).
5. _create_tools() gracefully skips unknown tool names.
6. _create_tools() gracefully handles tools that fail to initialize.
"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from shellbot2.tools import TOOL_REGISTRY, get_available_tool_names


# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------

class TestToolRegistry:
    """Tests for the TOOL_REGISTRY and helper functions."""

    EXPECTED_TOOLS = {
        "shell",
        "reader",
        "clipboard",
        "python",
        "tavilysearch",
        "fastmail",
        "calendar",
        "image-generator",
        "memory",
        "document-store",
        "subtasks",
        "conversation-search",
        "file_search",
        "text_replace",
    }

    def test_registry_contains_all_expected_tools(self):
        """Every tool we ship should be in the registry."""
        assert self.EXPECTED_TOOLS == set(TOOL_REGISTRY.keys())

    def test_registry_values_are_callable(self):
        """Every registry entry should be a callable factory."""
        for name, factory in TOOL_REGISTRY.items():
            assert callable(factory), f"Registry entry for '{name}' is not callable"

    def test_get_available_tool_names_returns_sorted_list(self):
        """get_available_tool_names() should return a sorted list."""
        names = get_available_tool_names()
        assert names == sorted(names)
        assert set(names) == self.EXPECTED_TOOLS

    def test_simple_tool_factory_returns_instance(self):
        """Simple tools (no special config) should instantiate with empty config."""
        for name in ("shell", "reader", "clipboard", "python", "memory",
                      "file_search", "text_replace"):
            tool = TOOL_REGISTRY[name]({})
            assert hasattr(tool, "name"), f"Tool '{name}' has no 'name' attribute"
            assert hasattr(tool, "__call__"), f"Tool '{name}' is not callable"

    def test_subtask_factory_requires_datadir(self):
        """The subtasks factory requires _datadir in config."""
        import tempfile
        cfg = {
            "_datadir": Path(tempfile.mkdtemp()),
            "_zmq_input_address": "tcp://127.0.0.1:9999",
        }
        tool = TOOL_REGISTRY["subtasks"](cfg)
        assert tool.name == "subtasks"


# ---------------------------------------------------------------------------
# Dynamic loading tests (via ShellBot3._create_tools)
# ---------------------------------------------------------------------------

class TestDynamicToolLoading:
    """Tests for ShellBot3._create_tools() dynamic loading behavior."""

    def _make_bot(self, tools_conf, tmp_path):
        """Helper to create a ShellBot3 with a minimal config.

        We patch out the heavy dependencies (MessageHistory, agent init)
        so we can test _create_tools() in isolation.
        """
        from shellbot2.agent import ShellBot3

        # Write a minimal config
        conf = {
            "provider": "gemini",
            "model": "gemini-3-flash-preview",
            "instructions": "test",
            "input_address": "tcp://127.0.0.1:5555",
        }
        if tools_conf is not None:
            conf["tools"] = tools_conf

        import yaml
        conf_path = tmp_path / "agent_conf.yaml"
        conf_path.write_text(yaml.dump(conf))

        # Create dummy message_history db so SQLAlchemy doesn't complain
        with patch.object(ShellBot3, '_initialize_agent', return_value=MagicMock()):
            bot = ShellBot3(datadir=tmp_path, thread_id="test-thread-123")

        return bot

    def test_loads_only_requested_tools(self, tmp_path):
        """When config lists specific tools, only those should be loaded."""
        bot = self._make_bot(["shell", "python", "clipboard"], tmp_path)
        tools = bot._create_tools()

        tool_names = {t.name for t in tools}
        assert tool_names == {"shell", "python", "clipboard"}

    def test_loads_all_tools_when_no_config(self, tmp_path):
        """When config has no 'tools' key, all tools should be loaded."""
        bot = self._make_bot(None, tmp_path)
        tools = bot._create_tools()

        # Should load many tools (at least the simple ones that don't need API keys)
        tool_names = {t.name for t in tools}
        # At minimum these simple tools should always succeed
        assert "shell" in tool_names
        assert "python" in tool_names
        assert "clipboard" in tool_names
        assert "memory" in tool_names

    def test_loads_tool_with_sub_config(self, tmp_path):
        """Tools specified as dicts should receive their sub-config."""
        # We mock DocStoreTool to avoid needing a real API key
        mock_docstore = MagicMock()
        mock_docstore.name = "document-store"
        mock_docstore.description = "mock"
        mock_docstore.parameters = {
            "type": "object",
            "properties": {
                "operation": {"type": "string"}
            },
            "required": ["operation"],
        }

        with patch.dict(TOOL_REGISTRY, {
            "document-store": lambda cfg: mock_docstore
        }):
            tools_conf = [
                "shell",
                {"document-store": {"store_id": "test-store-123"}},
            ]
            bot = self._make_bot(tools_conf, tmp_path)
            tools = bot._create_tools()

            tool_names = {t.name for t in tools}
            assert "shell" in tool_names
            assert "document-store" in tool_names

    def test_skips_unknown_tool_names(self, tmp_path):
        """Unknown tool names should be skipped with a warning, not crash."""
        bot = self._make_bot(["shell", "nonexistent_tool", "python"], tmp_path)
        tools = bot._create_tools()

        tool_names = {t.name for t in tools}
        assert tool_names == {"shell", "python"}

    def test_skips_tools_that_fail_to_init(self, tmp_path):
        """If a tool factory raises, the tool is skipped gracefully."""
        def broken_factory(cfg):
            raise RuntimeError("Intentional test failure")

        with patch.dict(TOOL_REGISTRY, {"broken-tool": broken_factory}):
            bot = self._make_bot(["shell", "broken-tool", "python"], tmp_path)
            tools = bot._create_tools()

            tool_names = {t.name for t in tools}
            assert tool_names == {"shell", "python"}

    def test_empty_tools_list_loads_nothing(self, tmp_path):
        """An explicit empty tools list should load zero tools."""
        bot = self._make_bot([], tmp_path)
        tools = bot._create_tools()
        assert len(tools) == 0

    def test_single_tool(self, tmp_path):
        """Config with a single tool should work fine."""
        bot = self._make_bot(["memory"], tmp_path)
        tools = bot._create_tools()

        assert len(tools) == 1
        assert tools[0].name == "memory"
