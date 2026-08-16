import asyncio
from collections import namedtuple
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from shellbot2.sensorframework.discovery import discover_sensor_specs
from shellbot2.sensors.disk_usage import DiskUsageSensor, SENSOR_SPECS
from shellbot2.sensorframework.sensor_spec import SensorRuntime


Usage = namedtuple("usage", "total used free")


class MemoryState:
    def __init__(self) -> None:
        self._data: dict = {}

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def set(self, key: str, value) -> None:
        self._data[key] = value

    def delete(self, key: str) -> None:
        self._data.pop(key, None)


def _runtime(tmp_path: Path, config: dict | None = None, now=None) -> SensorRuntime:
    return SensorRuntime(
        datadir=tmp_path,
        sensor_name="disk_usage",
        config=config or {},
        state=MemoryState(),
        logger=MagicMock(),
        now=now or (lambda: datetime(2026, 1, 1, tzinfo=timezone.utc)),
    )


def test_packaged_discovery_includes_disk_usage():
    specs = discover_sensor_specs()
    assert "disk_usage" in specs
    assert specs["disk_usage"].default_interval_seconds == 3600
    assert SENSOR_SPECS[0].name == "disk_usage"


def test_emits_warning_when_free_space_is_below_ten_percent(tmp_path):
    async def body():
        sensor = DiskUsageSensor(
            disk_usage=lambda path: Usage(total=1000, used=950, free=50),
        )
        observations = await sensor.poll(_runtime(tmp_path, {"path": "/"}))
        assert len(observations) == 1
        observation = observations[0]
        assert observation.kind == "low_disk_space"
        assert observation.severity == "warning"
        assert observation.dedupe_key == "low_disk:/"
        assert observation.payload["free_percent"] == 5.0
        assert observation.payload["min_free_percent"] == 10.0
        assert "5.0%" in observation.summary
        assert observation.payload["path"] == "/"

    asyncio.run(body())


def test_does_not_emit_when_free_space_meets_threshold(tmp_path):
    async def body():
        sensor = DiskUsageSensor(
            disk_usage=lambda path: Usage(total=1000, used=900, free=100),
        )
        observations = await sensor.poll(_runtime(tmp_path, {"path": "/"}))
        assert observations == []

    asyncio.run(body())


def test_custom_path_and_threshold(tmp_path):
    async def body():
        seen: list[str] = []

        def fake_usage(path: str):
            seen.append(path)
            return Usage(total=200, used=190, free=10)

        sensor = DiskUsageSensor(disk_usage=fake_usage)
        runtime = _runtime(tmp_path, {"path": "/data", "min_free_percent": 20})
        observations = await sensor.poll(runtime)
        assert seen == ["/data"]
        assert observations[0].payload["min_free_percent"] == 20.0
        assert observations[0].payload["free_percent"] == 5.0
        assert runtime.state.get("last_sample")["path"] == "/data"

    asyncio.run(body())


def test_stat_failure_returns_no_observation(tmp_path):
    async def body():
        def boom(path: str):
            raise OSError("nope")

        sensor = DiskUsageSensor(disk_usage=boom)
        runtime = _runtime(tmp_path, {"path": "/"})
        assert await sensor.poll(runtime) == []
        runtime.logger.exception.assert_called()

    asyncio.run(body())


@pytest.mark.parametrize("min_free_percent", [0, 101, True, "10", None])
def test_invalid_threshold_is_ignored(tmp_path, min_free_percent):
    async def body():
        sensor = DiskUsageSensor(
            disk_usage=lambda path: Usage(total=100, used=99, free=1),
        )
        runtime = _runtime(tmp_path, {"path": "/", "min_free_percent": min_free_percent})
        assert await sensor.poll(runtime) == []

    asyncio.run(body())
