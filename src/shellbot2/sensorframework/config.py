"""Declarative sensor configuration parsed from ``agent_conf.yaml``."""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import logging

from shellbot2.sensorframework.sensor_spec import (
    DEFAULT_INTERVAL_SECONDS,
    SensorSpec,
    validate_sensor_name,
)


logger = logging.getLogger(__name__)

DEFAULT_STATE_DB = "sensor_state.db"
DEFAULT_QUEUE_MAXSIZE = 100
DEFAULT_COOLDOWN_SECONDS = 0


@dataclass(frozen=True, slots=True)
class ResolvedSensorEntry:
    """An enabled sensor entry resolved against a discovered plugin spec."""

    name: str
    spec: SensorSpec
    interval_seconds: int
    cooldown_seconds: int
    thread_id: str
    config: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class SensorsConfig:
    """Validated sensors section from agent configuration.

    When sensors are disabled, ``enabled`` is False and ``entries`` is empty.
    """

    enabled: bool
    state_db_path: Path
    default_interval_seconds: int
    queue_maxsize: int
    entries: tuple[ResolvedSensorEntry, ...]


def sensors_section_enabled(conf: Mapping[str, Any] | None) -> bool:
    """Return True only when the config explicitly enables sensors."""

    if not conf:
        return False
    section = conf.get("sensors")
    return isinstance(section, Mapping) and section.get("enabled") is True


def parse_sensors_config(
    sensors_section: Any,
    *,
    datadir: Path,
    available_specs: Mapping[str, SensorSpec],
) -> SensorsConfig:
    """Parse and validate a ``sensors`` mapping from ``agent_conf.yaml``.

    Raises:
        ValueError: if the section is enabled but malformed, or an enabled
            entry names an unknown plugin.
    """

    if sensors_section is None:
        return _disabled_config(datadir)
    if not isinstance(sensors_section, Mapping):
        raise ValueError("sensors configuration must be a mapping")

    enabled = sensors_section.get("enabled", False)
    if enabled is not True:
        return _disabled_config(datadir, raw=sensors_section)

    default_interval_seconds = _parse_positive_int(
        sensors_section.get("default_interval_seconds", DEFAULT_INTERVAL_SECONDS),
        name="sensors.default_interval_seconds",
    )
    queue_maxsize = _parse_positive_int(
        sensors_section.get("queue_maxsize", DEFAULT_QUEUE_MAXSIZE),
        name="sensors.queue_maxsize",
    )
    state_db_path = _resolve_state_db(sensors_section.get("state_db", DEFAULT_STATE_DB), datadir)

    raw_entries = sensors_section.get("entries", [])
    if raw_entries is None:
        raw_entries = []
    if not isinstance(raw_entries, list):
        raise ValueError("sensors.entries must be a list")

    seen_names: set[str] = set()
    resolved: list[ResolvedSensorEntry] = []
    for index, raw_entry in enumerate(raw_entries):
        entry = _parse_entry(
            raw_entry,
            index=index,
            available_specs=available_specs,
            default_interval_seconds=default_interval_seconds,
            seen_names=seen_names,
        )
        if entry is not None:
            resolved.append(entry)

    return SensorsConfig(
        enabled=True,
        state_db_path=state_db_path,
        default_interval_seconds=default_interval_seconds,
        queue_maxsize=queue_maxsize,
        entries=tuple(resolved),
    )


def _disabled_config(datadir: Path, raw: Mapping[str, Any] | None = None) -> SensorsConfig:
    state_db = DEFAULT_STATE_DB
    if raw is not None and isinstance(raw.get("state_db"), str):
        state_db = raw["state_db"]
    return SensorsConfig(
        enabled=False,
        state_db_path=_resolve_state_db(state_db, datadir),
        default_interval_seconds=DEFAULT_INTERVAL_SECONDS,
        queue_maxsize=DEFAULT_QUEUE_MAXSIZE,
        entries=(),
    )


def _resolve_state_db(state_db: Any, datadir: Path) -> Path:
    if not isinstance(state_db, str) or not state_db.strip():
        raise ValueError("sensors.state_db must be a non-empty string")
    path = Path(state_db)
    if not path.is_absolute():
        path = Path(datadir) / path
    return path


def _parse_entry(
    raw_entry: Any,
    *,
    index: int,
    available_specs: Mapping[str, SensorSpec],
    default_interval_seconds: int,
    seen_names: set[str],
) -> ResolvedSensorEntry | None:
    location = f"sensors.entries[{index}]"
    if not isinstance(raw_entry, Mapping):
        raise ValueError(f"{location} must be a mapping")
    if "name" not in raw_entry:
        raise ValueError(f"{location} is missing required field 'name'")

    name = raw_entry["name"]
    try:
        name = validate_sensor_name(name)
    except ValueError as exc:
        raise ValueError(f"{location}.name is invalid: {exc}") from exc
    if name in seen_names:
        raise ValueError(f"duplicate sensor entry {name!r}")
    seen_names.add(name)

    enabled = raw_entry.get("enabled", True)
    if enabled is not True and enabled is not False:
        raise ValueError(f"{location}.enabled must be a boolean")

    spec = available_specs.get(name)
    if spec is None:
        if enabled:
            raise ValueError(
                f"sensor {name!r} is enabled but no plugin named {name!r} was found"
            )
        logger.warning(
            "Disabled sensor %r references an unavailable plugin",
            name,
        )
        return None
    if not enabled:
        return None

    if "interval_seconds" in raw_entry and raw_entry["interval_seconds"] is not None:
        interval_seconds = _parse_positive_int(
            raw_entry["interval_seconds"],
            name=f"{location}.interval_seconds",
        )
    else:
        interval_seconds = spec.default_interval_seconds or default_interval_seconds

    cooldown_seconds = _parse_non_negative_int(
        raw_entry.get("cooldown_seconds", DEFAULT_COOLDOWN_SECONDS),
        name=f"{location}.cooldown_seconds",
    )

    thread_id = raw_entry.get("thread_id", f"sensor:{name}")
    if not isinstance(thread_id, str) or not thread_id.strip():
        raise ValueError(f"{location}.thread_id must be a non-empty string")

    config = raw_entry.get("config", {})
    if config is None:
        config = {}
    if not isinstance(config, Mapping):
        raise ValueError(f"{location}.config must be a mapping")

    return ResolvedSensorEntry(
        name=name,
        spec=spec,
        interval_seconds=interval_seconds,
        cooldown_seconds=cooldown_seconds,
        thread_id=thread_id,
        config=dict(config),
    )


def _parse_positive_int(value: Any, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _parse_non_negative_int(value: Any, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value
