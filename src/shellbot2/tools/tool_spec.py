"""Contracts shared by built-in and dynamically discovered agent tools."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


class ToolCallable(Protocol):
    """A callable implementation used by a model-facing tool."""

    def __call__(self, **kwargs: Any) -> Any: ...


@dataclass(frozen=True, slots=True)
class ToolRuntime:
    """Dependencies supplied by the agent when creating a tool."""

    datadir: Path
    config: Mapping[str, Any]
    message_history: Any


ToolFactory = Callable[[ToolRuntime, Mapping[str, Any]], ToolCallable]


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """Declarative registration data for one model-facing tool.

    ``name`` is the YAML configuration key. ``function_name`` is the name
    exposed to the model and defaults to ``name`` when omitted.
    """

    name: str
    description: str
    parameters: Mapping[str, Any]
    factory: ToolFactory
    function_name: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("ToolSpec.name must be a non-empty string")
        if self.function_name is not None and (
            not isinstance(self.function_name, str) or not self.function_name
        ):
            raise ValueError("ToolSpec.function_name must be a non-empty string or None")
        if not isinstance(self.description, str) or not self.description:
            raise ValueError("ToolSpec.description must be a non-empty string")
        if not isinstance(self.parameters, Mapping):
            raise ValueError("ToolSpec.parameters must be a JSON schema mapping")
        if self.parameters.get("type") != "object":
            raise ValueError("ToolSpec.parameters must describe an object")
        if not callable(self.factory):
            raise ValueError("ToolSpec.factory must be callable")

    @property
    def model_name(self) -> str:
        """Return the model-facing function name."""
        return self.function_name or self.name
