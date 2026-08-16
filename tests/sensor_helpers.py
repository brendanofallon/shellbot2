"""Shared fake sensors and clocks for framework tests."""

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
import asyncio

from shellbot2.sensorframework.sensor_spec import SensorObservation, SensorSpec


async def wait_until(predicate: Callable[[], bool], timeout: float = 2.0) -> None:
    async def _spin() -> None:
        while not predicate():
            await asyncio.sleep(0)

    await asyncio.wait_for(_spin(), timeout=timeout)


class FakeClock:
    def __init__(self, start: datetime | None = None) -> None:
        self.now = start or datetime(2026, 1, 1, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class SleepController:
    def __init__(self) -> None:
        self.intervals: list[float] = []
        self._pending: list[asyncio.Event] = []
        self._requested = asyncio.Event()

    async def sleep(self, seconds: float) -> None:
        event = asyncio.Event()
        self.intervals.append(seconds)
        self._pending.append(event)
        self._requested.set()
        await event.wait()

    async def wait_for_sleep(self) -> None:
        await asyncio.wait_for(self._requested.wait(), timeout=2)
        self._requested.clear()

    def release_one(self) -> None:
        for event in self._pending:
            if not event.is_set():
                event.set()
                return
        raise AssertionError("no pending sleep to release")


class RecordingSensor:
    def __init__(
        self,
        observations: list[list[SensorObservation]] | None = None,
        *,
        error_on: set[int] | None = None,
        gate: asyncio.Event | None = None,
        entered: asyncio.Event | None = None,
    ) -> None:
        self.observations = observations or []
        self.error_on = error_on or set()
        self.gate = gate
        self.entered = entered
        self.polls = 0
        self.runtimes = []
        self.concurrent = 0
        self.max_concurrent = 0

    async def poll(self, runtime):
        self.concurrent += 1
        self.max_concurrent = max(self.max_concurrent, self.concurrent)
        self.polls += 1
        self.runtimes.append(runtime)
        if self.entered is not None:
            self.entered.set()
        try:
            if self.polls in self.error_on:
                raise RuntimeError(f"poll {self.polls} failed")
            if self.gate is not None:
                await self.gate.wait()
            index = self.polls - 1
            if index < len(self.observations):
                return self.observations[index]
            return []
        finally:
            self.concurrent -= 1


def make_observation(
    *,
    kind: str = "condition",
    summary: str = "something happened",
    dedupe_key: str = "key-1",
    payload: dict | None = None,
    severity: str = "info",
) -> SensorObservation:
    return SensorObservation(
        kind=kind,
        summary=summary,
        dedupe_key=dedupe_key,
        payload=payload or {"ok": True},
        severity=severity,  # type: ignore[arg-type]
    )


def make_spec(
    name: str = "example_sensor",
    sensor: RecordingSensor | None = None,
    description: str = "A test sensor",
    default_interval_seconds: int = 300,
) -> tuple[SensorSpec, RecordingSensor]:
    instance = sensor or RecordingSensor()

    def factory(runtime):
        return instance

    spec = SensorSpec(
        name=name,
        description=description,
        factory=factory,
        default_interval_seconds=default_interval_seconds,
    )
    return spec, instance
