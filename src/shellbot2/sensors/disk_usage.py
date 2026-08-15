"""Illustrative sensor that watches free disk space.

This plugin only observes filesystem statistics via ``shutil.disk_usage``.
It does not run shell commands, and it does not call the agent.
"""

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol
import shutil

from shellbot2.sensorframework.sensor_spec import (
    DEDUPE_KEY_MAX_CHARS,
    SensorObservation,
    SensorRuntime,
    SensorSpec,
)


DEFAULT_MIN_FREE_PERCENT = 10.0
DEFAULT_INTERVAL_SECONDS = 3600


class DiskUsageResult(Protocol):
    total: int
    used: int
    free: int


DiskUsageFn = Callable[[str], DiskUsageResult]


def _default_path(runtime: SensorRuntime) -> str:
    return str(Path(runtime.datadir).expanduser().resolve().anchor or "/")


def _resolve_path(runtime: SensorRuntime) -> str | None:
    raw = runtime.config.get("path")
    if raw is None:
        return _default_path(runtime)
    if not isinstance(raw, str) or not raw.strip():
        runtime.logger.warning("disk_usage config path must be a non-empty string")
        return None
    return str(Path(raw).expanduser())


def _resolve_min_free_percent(runtime: SensorRuntime) -> float | None:
    raw = runtime.config.get("min_free_percent", DEFAULT_MIN_FREE_PERCENT)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        runtime.logger.warning("disk_usage config min_free_percent must be a number")
        return None
    value = float(raw)
    if value <= 0 or value > 100:
        runtime.logger.warning("disk_usage config min_free_percent must be in (0, 100]")
        return None
    return value


def _format_bytes(num_bytes: int) -> str:
    gib = num_bytes / (1024 ** 3)
    if gib >= 1:
        return f"{gib:.1f} GiB"
    mib = num_bytes / (1024 ** 2)
    return f"{mib:.1f} MiB"


def _dedupe_key(path: str) -> str:
    key = f"low_disk:{path}"
    if len(key) > DEDUPE_KEY_MAX_CHARS:
        key = f"low_disk:{path[: DEDUPE_KEY_MAX_CHARS - 9]}"
    return key.replace("\n", " ").replace("\r", " ")


class DiskUsageSensor:
    """Emit an observation when free space on a volume is below a threshold."""

    def __init__(self, disk_usage: DiskUsageFn | None = None) -> None:
        self._disk_usage = disk_usage or shutil.disk_usage

    async def poll(self, runtime: SensorRuntime) -> Sequence[SensorObservation]:
        path = _resolve_path(runtime)
        min_free_percent = _resolve_min_free_percent(runtime)
        if path is None or min_free_percent is None:
            return []

        try:
            usage = self._disk_usage(path)
        except OSError:
            runtime.logger.exception("disk_usage could not stat %s", path)
            return []

        if usage.total <= 0:
            runtime.logger.warning("disk_usage reported non-positive total bytes for %s", path)
            return []

        free_percent = 100.0 * usage.free / usage.total
        runtime.state.set(
            "last_sample",
            {
                "path": path,
                "free_percent": round(free_percent, 2),
                "free_bytes": usage.free,
                "total_bytes": usage.total,
            },
        )
        if free_percent >= min_free_percent:
            return []

        rounded = round(free_percent, 2)
        summary = (
            f"Free disk space on {path} is {rounded}% "
            f"({_format_bytes(usage.free)} free of {_format_bytes(usage.total)}); "
            f"threshold is {min_free_percent:g}%."
        )
        return [
            SensorObservation(
                kind="low_disk_space",
                summary=summary,
                dedupe_key=_dedupe_key(path),
                payload={
                    "path": path,
                    "free_percent": rounded,
                    "min_free_percent": min_free_percent,
                    "free_bytes": usage.free,
                    "used_bytes": usage.used,
                    "total_bytes": usage.total,
                },
                occurred_at=runtime.now(),
                severity="warning",
            )
        ]


def _factory(runtime: SensorRuntime) -> DiskUsageSensor:
    return DiskUsageSensor()


SENSOR_SPECS = (
    SensorSpec(
        name="disk_usage",
        description=(
            "Watches free space on a filesystem path and reports when it "
            "drops below a configured percentage (default 10%)."
        ),
        factory=_factory,
        default_interval_seconds=DEFAULT_INTERVAL_SECONDS,
    ),
)
