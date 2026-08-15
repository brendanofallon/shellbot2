"""Envelope for work queued to the daemon agent worker."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
import json


@dataclass
class InputMessage:
    """Schema for incoming messages from ZeroMQ and internal sensor events.

    External clients must continue to send ``prompt``, ``source``, and
    ``datetime``. ``event_id`` and ``metadata`` are optional and default to
    unset/empty so existing ZeroMQ clients remain valid.
    """

    prompt: str
    source: str
    datetime: str
    thread_id: str | None = None
    event_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_json(cls, json_str: str) -> "InputMessage":
        """Parse an InputMessage from a JSON string.

        Args:
            json_str: JSON-encoded string with prompt, source, and datetime fields.

        Returns:
            An InputMessage instance.

        Raises:
            ValueError: If required fields are missing or types are invalid.
            json.JSONDecodeError: If the string is not valid JSON.
        """

        data = json.loads(json_str)
        if not isinstance(data, dict):
            raise ValueError("Input message must be a JSON object")
        required_fields = {"prompt", "source", "datetime"}
        missing = required_fields - set(data.keys())
        if missing:
            raise ValueError(f"Missing required fields: {missing}")
        return cls(
            prompt=_require_str(data["prompt"], "prompt"),
            source=_require_str(data["source"], "source"),
            datetime=_require_str(data["datetime"], "datetime"),
            thread_id=_optional_str(data.get("thread_id"), "thread_id"),
            event_id=_optional_str(data.get("event_id"), "event_id"),
            metadata=_validate_metadata(data.get("metadata", {})),
        )


def _require_str(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    return value


def _optional_str(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string or null")
    return value


def _validate_metadata(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("metadata must be a JSON object")
    if not all(isinstance(key, str) for key in value):
        raise ValueError("metadata keys must be strings")
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"metadata must be JSON-serializable: {exc}") from exc
    return value
