"""
Tools package for shellbot.

This package contains all tool modules that can be used by assistants.

The TOOL_REGISTRY maps tool names (as used in agent_conf.yaml) to factory
callables.  Each factory accepts a single ``tool_config`` dict (which may be
empty) and returns an instantiated tool object ready to be wrapped with
``create_tool_from_schema``.

Tools that require runtime dependencies (datadir, zmq address, message
history) accept them through ``tool_config`` — the agent injects these before
calling the factory.
"""

from . import botfunctions, memorytool, docstoretool
from . import fastmailtool, cal, imagetool, conversationsearchtool
from . import filesearchtool

from .botfunctions import (
    ShellFunction,
    ReaderFunction,
    ClipboardFunction,
    PythonFunction,
    TavilySearchFunction,
)
from .fastmailtool import FastmailTool
from .cal import CalendarTool
from .imagetool import ImageTool
from .memorytool import MemoryFunction
from .docstoretool import DocStoreTool
from .conversationsearchtool import ConversationSearchTool
from .subtasktool import SubTaskTool
from .filesearchtool import FileSearchFunction, TextReplaceFunction

from typing import Any, Callable, Dict


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------
# Each value is a callable:  (tool_config: dict) -> tool_instance
#
# ``tool_config`` is the per-tool configuration from agent_conf.yaml.  For
# simple tools it will be an empty dict.  For tools with extra settings
# (e.g. document-store with a store_id) it will contain those keys.
#
# Some tools need runtime objects that are not part of the YAML config (e.g.
# the subtask tool needs the datadir path and zmq address).  These are
# injected into ``tool_config`` by the agent before looking up the factory.
# By convention these injected keys start with an underscore:
#   _datadir          -> pathlib.Path
#   _zmq_input_address -> str
#   _message_history  -> MessageHistory instance
# ---------------------------------------------------------------------------

TOOL_REGISTRY: Dict[str, Callable[[Dict[str, Any]], Any]] = {
    "shell":                lambda cfg: ShellFunction(),
    "reader":              lambda cfg: ReaderFunction(),
    "clipboard":           lambda cfg: ClipboardFunction(),
    "python":              lambda cfg: PythonFunction(),
    "tavilysearch":        lambda cfg: TavilySearchFunction(),
    "fastmail":            lambda cfg: FastmailTool(),
    "calendar":            lambda cfg: CalendarTool(),
    "image-generator":     lambda cfg: ImageTool(),
    "memory":              lambda cfg: MemoryFunction(),
    "document-store":      lambda cfg: DocStoreTool(store_id=cfg.get("store_id")),
    "subtasks":            lambda cfg: SubTaskTool(
                                cfg["_datadir"] / "subtask_modules",
                                cfg.get("_zmq_input_address", "tcp://127.0.0.1:5555"),
                            ),
    "conversation-search": lambda cfg: ConversationSearchTool(
                                message_history=cfg["_message_history"],
                            ),
    "file_search":         lambda cfg: FileSearchFunction(),
    "text_replace":        lambda cfg: TextReplaceFunction(),
}


def get_available_tool_names():
    """Return a sorted list of all registered tool names."""
    return sorted(TOOL_REGISTRY.keys())


__all__ = [
    'botfunctions',
    'memorytool',
    'docstoretool',
    'fastmailtool',
    'cal',
    'imagetool',
    'conversationsearchtool',
    'filesearchtool',
    'TOOL_REGISTRY',
    'get_available_tool_names',
]
