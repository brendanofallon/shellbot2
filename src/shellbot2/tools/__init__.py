"""
Tools package for shellbot.

This package contains all tool modules that can be used by assistants.
"""

from .discovery import discover_tool_specs
from .tool_spec import ToolRuntime, ToolSpec

__all__ = [
    "ToolRuntime",
    "ToolSpec",
    "discover_tool_specs",
]
