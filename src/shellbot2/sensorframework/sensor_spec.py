"""Public contracts for ShellBot2 sensor plugins.

Sensors emit structured facts, never executable instructions. A plugin returns
typed observations; only framework-owned code may turn those observations into
a prompt for the daemon's conversation agent. Plugins must not call that agent,
ZeroMQ, or the event dispatcher. A plugin may use a separate, tightly scoped
LLM for local inference when its implementation explicitly provides one.
"""

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal, Protocol, Self, runtime_checkable
import json
import logging
import re

from pydantic import BaseModel, ConfigDict, Field, SkipValidation, StrictInt, field_validator, model_validator

JSONValue = str | int | float | bool | None | list["JSONValue"] | dict[str, "JSONValue"]

SENSOR_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
DEFAULT_INTERVAL_SECONDS = 300
KIND_MAX_CHARS = 128
SUMMARY_MAX_CHARS = 2000
DEDUPE_KEY_MAX_CHARS = 256
PAYLOAD_MAX_BYTES = 16_384
STATE_KEY_MAX_CHARS = 256

Severity = Literal["info", "warning", "critical"]
SEVERITIES: frozenset[str] = frozenset({"info", "warning", "critical"})


def validate_json_value(value: Any, *, name: str = "value") -> JSONValue:
    """Return ``value`` if it is JSON-serializable with strict JSON numbers.

    Observations and sensor state may only contain JSON-safe data. This check
    is a data boundary, not an execution boundary: passing it does not make a
    payload trustworthy as instructions.
    """

    try:
        serialized = json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be JSON-serializable: {exc}") from exc
    if len(serialized.encode("utf-8")) > PAYLOAD_MAX_BYTES:
        raise ValueError(
            f"{name} exceeds the {PAYLOAD_MAX_BYTES}-byte JSON size limit"
        )
    loaded = json.loads(serialized)
    return loaded


def validate_sensor_name(name: Any, *, field_name: str = "name") -> str:
    """Validate a sensor identifier used in configuration and state namespaces."""

    if not isinstance(name, str) or not SENSOR_NAME_PATTERN.fullmatch(name):
        raise ValueError(
            f"{field_name} must be a letter followed by up to 63 letters, "
            "digits, underscores, or hyphens"
        )
    return name


def _require_single_line(value: Any, *, field_name: str, max_chars: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    if "\n" in value or "\r" in value:
        raise ValueError(f"{field_name} must be a single line")
    if len(value) > max_chars:
        raise ValueError(f"{field_name} must be at most {max_chars} characters")
    return value


class SensorStateStore(Protocol):
    """Durable key-value store namespaced to a single sensor.

    Values must be JSON-serializable. Implementations must not expose SQL or
    other sensors' namespaces. Framework-reserved keys are not writable through
    this interface.
    """

    def get(self, key: str, default: JSONValue | None = None) -> JSONValue | None:
        """Return the stored value for ``key``, or ``default`` if missing."""

    def set(self, key: str, value: JSONValue) -> None:
        """Store a JSON-safe value for ``key``."""

    def delete(self, key: str) -> None:
        """Remove ``key`` if it exists."""


class SensorRuntime(BaseModel):
    """Dependencies supplied by the framework when constructing and polling a sensor.

    ``config`` is this sensor's configuration block only, not the full agent
    configuration. ``state`` is already bound to ``sensor_name``.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    datadir: Path
    sensor_name: str
    config: Mapping[str, Any]
    state: Annotated[SensorStateStore, SkipValidation()]
    logger: Annotated[logging.Logger, SkipValidation()]
    now: Annotated[Callable[[], datetime], SkipValidation()]


class SensorObservation(BaseModel):
    """A structured fact produced by a sensor poll.

    Observation fields are data, never executable instructions. ``dedupe_key``
    is consumed by the framework for delivery suppression and is not included
    in the agent-facing prompt as an instruction.
    """

    model_config = ConfigDict(frozen=True)

    kind: str
    summary: str
    dedupe_key: str
    payload: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime | None = None
    severity: Severity = "info"

    @field_validator("kind")
    @classmethod
    def _validate_kind(cls, value: str) -> str:
        return _require_single_line(value, field_name="kind", max_chars=KIND_MAX_CHARS)

    @field_validator("summary")
    @classmethod
    def _validate_summary(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("summary must be a non-empty string")
        if len(value) > SUMMARY_MAX_CHARS:
            raise ValueError(f"summary must be at most {SUMMARY_MAX_CHARS} characters")
        return value

    @field_validator("dedupe_key")
    @classmethod
    def _validate_dedupe_key(cls, value: str) -> str:
        return _require_single_line(
            value, field_name="dedupe_key", max_chars=DEDUPE_KEY_MAX_CHARS
        )

    @field_validator("payload")
    @classmethod
    def _validate_payload(cls, value: Any) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise ValueError("payload must be a mapping")
        canonical_payload = validate_json_value(dict(value), name="payload")
        if not isinstance(canonical_payload, dict):
            raise ValueError("payload must be a JSON object")
        return canonical_payload

    @field_validator("severity", mode="before")
    @classmethod
    def _validate_severity(cls, value: Any) -> Severity:
        if value not in SEVERITIES:
            raise ValueError("severity must be one of 'info', 'warning', or 'critical'")
        return value  # type: ignore[return-value]


@runtime_checkable
class Sensor(Protocol):
    """A polling plugin that returns zero or more observations.

    Return an empty sequence when nothing notable has happened. Do not call the
    daemon's conversation agent, ZeroMQ, or the event dispatcher.
    """

    async def poll(self, runtime: SensorRuntime) -> Sequence[SensorObservation]:
        """Observe the data source and return structured facts."""


SensorFactory = Callable[[SensorRuntime], Sensor]


class SensorSpec(BaseModel):
    """Declarative registration data for one sensor plugin.

    ``name`` is the YAML configuration key and the state-store namespace.
    ``default_interval_seconds`` is used when the sensor entry omits
    ``interval_seconds`` (default 300).
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    name: str
    description: str
    factory: Annotated[SensorFactory, SkipValidation()]
    default_interval_seconds: StrictInt = DEFAULT_INTERVAL_SECONDS

    @model_validator(mode="after")
    def _validate_spec(self) -> Self:
        validate_sensor_name(self.name)
        if not self.description.strip():
            raise ValueError("SensorSpec.description must be a non-empty string")
        if not callable(self.factory):
            raise ValueError("SensorSpec.factory must be callable")
        if self.default_interval_seconds <= 0:
            raise ValueError("SensorSpec.default_interval_seconds must be a positive integer")
        return self
