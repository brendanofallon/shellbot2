
from unittest.mock import MagicMock, patch

from shellbot2.agent import ShellBot3
from shellbot2.tools.discovery import discover_tool_specs
from shellbot2.tools.tool_spec import ToolRuntime, ToolSpec


from pathlib import Path
from unittest.mock import patch, MagicMock
from shellbot2.agent import ShellBot3, create_azure_provider, _is_azure_foundry_v1_endpoint
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.providers.azure import AzureProvider



class EchoTool:
    def __init__(self, result: str = "ok"):
        self.result = result

    def __call__(self, **kwargs):
        return self.result


def make_spec(name: str, *, function_name: str | None = None, calls: list | None = None):
    def factory(runtime, kwargs):
        if calls is not None:
            calls.append((name, runtime, dict(kwargs)))
        return EchoTool(name)

    return ToolSpec(
        name=name,
        function_name=function_name,
        description=f"{name} tool",
        parameters={"type": "object", "properties": {}, "required": []},
        factory=factory,
    )


def test_packaged_tool_discovery_exposes_explicit_specs():
    specs = discover_tool_specs()

    assert {
        "shell",
        "reader",
        "clipboard",
        "python",
        "tavilysearch",
        "fastmail",
        "calendar",
        "image-generator",
        "image-reader",
        "memory",
        "document-store",
        "conversation-search",
        "subtasks",
        "file-search",
        "text_replace",
        "notes",
    } <= specs.keys()
    assert specs["file-search"].model_name == "file_search"


def test_custom_tool_specs_load_and_override_packaged_specs(tmp_path):
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "custom_tool.py").write_text(
        """
from shellbot2.tools.tool_spec import ToolSpec

class CustomTool:
    def __init__(self, prefix="custom"):
        self.prefix = prefix

    def __call__(self, **kwargs):
        return self.prefix

TOOL_SPECS = (
    ToolSpec(
        name="shell",
        description="Custom shell replacement",
        parameters={"type": "object", "properties": {}, "required": []},
        factory=lambda _runtime, kwargs: CustomTool(**kwargs),
    ),
    ToolSpec(
        name="my-custom-tool",
        description="A custom tool",
        parameters={"type": "object", "properties": {}, "required": []},
        factory=lambda _runtime, kwargs: CustomTool(**kwargs),
    ),
)
""".strip()
    )

    specs = discover_tool_specs(tools_dir)
    runtime = ToolRuntime(tmp_path, {}, MagicMock())

    assert specs["shell"].description == "Custom shell replacement"
    assert specs["my-custom-tool"].factory(runtime, {"prefix": "loaded"})() == "loaded"

    with (
        patch("shellbot2.agent.load_conf", return_value={"tools": [{"my-custom-tool": {"prefix": "agent"}}]}),
        patch("shellbot2.agent.MessageHistory"),
        patch.object(ShellBot3, "_initialize_agent") as mock_initialize_agent,
    ):
        ShellBot3(datadir=tmp_path)

    tools = mock_initialize_agent.call_args.args[1]
    assert [tool.name for tool in tools] == ["my-custom-tool"]


def test_discovery_skips_failed_modules_and_duplicate_custom_specs(tmp_path, caplog):
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "broken.py").write_text("raise RuntimeError('broken plugin')")
    (tools_dir / "first.py").write_text(
        """
from shellbot2.tools.tool_spec import ToolSpec
TOOL_SPECS = (
    ToolSpec(
        name="duplicate",
        description="first",
        parameters={"type": "object", "properties": {}, "required": []},
        factory=lambda _runtime, _kwargs: lambda: "first",
    ),
)
""".strip()
    )
    (tools_dir / "second.py").write_text(
        """
from shellbot2.tools.tool_spec import ToolSpec
TOOL_SPECS = (
    ToolSpec(
        name="duplicate",
        description="second",
        parameters={"type": "object", "properties": {}, "required": []},
        factory=lambda _runtime, _kwargs: lambda: "second",
    ),
)
""".strip()
    )
    (tools_dir / "invalid.py").write_text("TOOL_SPECS = (object(),)")

    specs = discover_tool_specs(tools_dir)

    assert specs["duplicate"].description == "first"
    assert "Failed to import tool module" in caplog.text
    assert "Ignoring duplicate tool spec" in caplog.text
    assert "Ignoring invalid tool spec" in caplog.text


def test_agent_uses_spec_factories_for_configured_tools(tmp_path):
    calls = []
    specs = {
        "shell": make_spec("shell", calls=calls),
        "document-store": make_spec("document-store", calls=calls),
        "python": make_spec("python", calls=calls),
    }
    history = MagicMock()

    with (
        patch("shellbot2.agent.load_conf") as mock_load_conf,
        patch("shellbot2.agent.MessageHistory", return_value=history),
        patch("shellbot2.agent.discover_tool_specs", return_value=specs),
        patch.object(ShellBot3, "_initialize_agent") as mock_initialize_agent,
    ):
        mock_load_conf.return_value = {
            "tools": [
                "shell",
                {"document-store": {"store_id": "test-store-id"}},
                "python",
            ]
        }

        ShellBot3(datadir=tmp_path)

    tools = mock_initialize_agent.call_args.args[1]
    assert [tool.name for tool in tools] == ["shell", "document-store", "python"]
    assert calls[1][2] == {"store_id": "test-store-id"}
    assert all(call[1].message_history is history for call in calls)


def test_agent_uses_all_discovered_tools_when_tools_is_omitted(tmp_path):
    specs = {"alpha": make_spec("alpha"), "beta": make_spec("beta")}

    with (
        patch("shellbot2.agent.load_conf", return_value={}),
        patch("shellbot2.agent.MessageHistory"),
        patch("shellbot2.agent.discover_tool_specs", return_value=specs),
        patch.object(ShellBot3, "_initialize_agent") as mock_initialize_agent,
    ):
        ShellBot3(datadir=tmp_path)

    tools = mock_initialize_agent.call_args.args[1]
    assert [tool.name for tool in tools] == ["alpha", "beta"]


def test_context_dependent_factories_receive_agent_runtime(tmp_path, monkeypatch):
    from shellbot2.tools.conversationsearchtool import TOOL_SPECS as conversation_specs
    import shellbot2.tools.subtasktool as subtasktool

    history = MagicMock()
    runtime = ToolRuntime(tmp_path, {"input_address": "tcp://127.0.0.1:9999"}, history)

    conversation_tool = conversation_specs[0].factory(runtime, {})
    assert conversation_tool.message_history is history

    captured_kwargs = {}

    class FakeSubTaskTool:
        def __init__(self, **kwargs):
            captured_kwargs.update(kwargs)

    monkeypatch.setattr(subtasktool, "SubTaskTool", FakeSubTaskTool)
    subtasktool.TOOL_SPECS[0].factory(runtime, {})

    assert captured_kwargs == {
        "subtask_modules_dir": tmp_path / "subtask_modules",
        "zmq_input_address": "tcp://127.0.0.1:9999",
    }

    
    # Initialize bot
    bot = ShellBot3(datadir=tmp_path)
    
    # Check that tools were created based on config
    assert mock_init_agent.called
    tools_passed = mock_init_agent.call_args[0][1]
    
    # We should have exactly 2 tools loaded
    assert len(tools_passed) == 2
    
    tool_names = [tool.name for tool in tools_passed]
    assert 'shell' in tool_names
    assert 'my-custom-tool' in tool_names

def test_azure_foundry_v1_endpoint_detection():
    assert _is_azure_foundry_v1_endpoint("https://example.openai.azure.com/openai/v1/")
    assert _is_azure_foundry_v1_endpoint("https://example.openai.azure.com/openai/v1")
    assert not _is_azure_foundry_v1_endpoint("https://example.openai.azure.com/")

@patch.dict('os.environ', {
    'AZURE_FOUNDRY_ENDPOINT': 'https://example.openai.azure.com/openai/v1/',
    'AZURE_FOUNDRY_API_KEY': 'test-key',
}, clear=False)
def test_create_azure_provider_uses_openai_for_foundry_v1():
    provider = create_azure_provider({})
    assert isinstance(provider, OpenAIProvider)
    assert provider.base_url.rstrip('/') == 'https://example.openai.azure.com/openai/v1'

@patch.dict('os.environ', {
    'AZURE_OPENAI_ENDPOINT': 'https://example.openai.azure.com/',
    'AZURE_OPENAI_API_KEY': 'test-key',
    'OPENAI_API_VERSION': '2025-01-01-preview',
}, clear=False)
def test_create_azure_provider_uses_azure_for_classic_endpoint():
    provider = create_azure_provider({})
    assert isinstance(provider, AzureProvider)
    
