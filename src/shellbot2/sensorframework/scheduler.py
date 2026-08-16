"""Periodic sensor polling, delivery deduplication, and cooldown handling.

Delivery is recorded only after a successful enqueue onto the daemon work
queue. That is at-most-once successful queue insertion per cooldown window:
a crash after insertion and before the agent run completes may produce a
later duplicate. This is not end-to-end exactly-once processing.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Any
import asyncio
import logging

from shellbot2.input_message import InputMessage
from shellbot2.sensorframework.config import ResolvedSensorEntry
from shellbot2.sensorframework.prompt_builder import observation_to_input_message
from shellbot2.sensorframework.sensor_spec import Sensor, SensorObservation, SensorRuntime
from shellbot2.sensorframework.state_store import SqliteSensorStateStore


logger = logging.getLogger(__name__)


@dataclass
class SensorStatus:
    """Point-in-time snapshot of one scheduled sensor."""

    name: str
    next_poll_time: datetime | None = None
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    failure_count: int = 0
    last_delivery_at: datetime | None = None


@dataclass
class _SensorRuntimeState:
    entry: ResolvedSensorEntry
    status: SensorStatus
    sensor: Sensor | None = None
    task: asyncio.Task | None = None


class SensorScheduler:
    """Run one supervised polling task per enabled sensor.

    Each task instantiates its plugin once (retrying after the interval if
    construction fails), polls immediately at start, then waits for
    ``interval_seconds`` *after* each poll completes so polls cannot overlap.
    """

    def __init__(
        self,
        entries: Sequence[ResolvedSensorEntry],
        *,
        datadir: Path,
        state_store: SqliteSensorStateStore,
        enqueue: Callable[[InputMessage], bool],
        clock: Callable[[], datetime],
        sleep: Callable[[float], Any] | None = None,
    ) -> None:
        self._entries = tuple(entries)
        self._datadir = Path(datadir)
        self._state_store = state_store
        self._enqueue = enqueue
        self._clock = clock
        self._sleep = sleep or asyncio.sleep
        self._states: dict[str, _SensorRuntimeState] = {
            entry.name: _SensorRuntimeState(
                entry=entry,
                status=SensorStatus(name=entry.name),
            )
            for entry in self._entries
        }
        self._running = False

    async def start(self) -> None:
        """Launch one polling task per configured sensor."""

        if self._running:
            return
        self._running = True
        for state in self._states.values():
            state.task = asyncio.create_task(
                self._run_sensor(state),
                name=f"sensor:{state.entry.name}",
            )

    async def stop(self) -> None:
        """Cancel polling tasks and wait for them to finish. Idempotent."""

        self._running = False
        tasks = [state.task for state in self._states.values() if state.task is not None]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for state in self._states.values():
            state.task = None

    def status(self) -> dict[str, SensorStatus]:
        """Return a snapshot of scheduler state keyed by sensor name."""

        return {
            name: SensorStatus(
                name=state.status.name,
                next_poll_time=state.status.next_poll_time,
                last_success_at=state.status.last_success_at,
                last_failure_at=state.status.last_failure_at,
                failure_count=state.status.failure_count,
                last_delivery_at=state.status.last_delivery_at,
            )
            for name, state in self._states.items()
        }

    async def _run_sensor(self, state: _SensorRuntimeState) -> None:
        entry = state.entry
        runtime = self._make_runtime(entry)
        try:
            while self._running:
                state.status.next_poll_time = self._clock()
                await self._poll_once(state, runtime)
                if not self._running:
                    break
                state.status.next_poll_time = self._clock() + timedelta(
                    seconds=entry.interval_seconds
                )
                await self._sleep(entry.interval_seconds)
        except asyncio.CancelledError:
            raise

    def _make_runtime(self, entry: ResolvedSensorEntry) -> SensorRuntime:
        return SensorRuntime(
            datadir=self._datadir,
            sensor_name=entry.name,
            config=MappingProxyType(dict(entry.config)),
            state=self._state_store.bind(entry.name),
            logger=logging.getLogger(f"shellbot2.sensorframework.plugin.{entry.name}"),
            now=self._clock,
        )

    async def _poll_once(self, state: _SensorRuntimeState, runtime: SensorRuntime) -> None:
        entry = state.entry
        try:
            if state.sensor is None:
                state.sensor = entry.spec.factory(runtime)
            logger.info("Polling sensor %r", entry.name)
            observations = await state.sensor.poll(runtime)
            logger.info("Polled sensor %r; got %d observations", entry.name, len(observations))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Sensor %r poll/factory failed; will retry after interval", entry.name)
            state.status.last_failure_at = self._clock()
            state.status.failure_count += 1
            return

        if observations is None:
            logger.error("Sensor %r returned None from poll(); treating as failure", entry.name)
            state.status.last_failure_at = self._clock()
            state.status.failure_count += 1
            return

        try:
            observation_list = list(observations)
        except TypeError:
            logger.error(
                "Sensor %r poll() returned a non-iterable %s",
                entry.name,
                type(observations).__name__,
            )
            state.status.last_failure_at = self._clock()
            state.status.failure_count += 1
            return

        now = self._clock()
        for observation in observation_list:
            if not isinstance(observation, SensorObservation):
                logger.error(
                    "Sensor %r yielded a non-observation %s; skipping",
                    entry.name,
                    type(observation).__name__,
                )
                continue
            self._handle_observation(state, observation, now)

        state.status.last_success_at = now
        state.status.failure_count = 0

    def _handle_observation(
        self,
        state: _SensorRuntimeState,
        observation: SensorObservation,
        now: datetime,
    ) -> None:
        entry = state.entry
        record = self._state_store.get_delivery(entry.name, observation.dedupe_key) or {}
        last_delivered_at = _parse_optional_datetime(record.get("last_delivered_at"))

        updated = {
            "last_observed_at": now.isoformat(),
            "last_delivered_at": record.get("last_delivered_at"),
            "last_event_id": record.get("last_event_id"),
        }

        if last_delivered_at is not None:
            elapsed = (now - last_delivered_at).total_seconds()
            if elapsed < entry.cooldown_seconds:
                logger.info(
                    "Skipping sensor %r observation kind=%s dedupe_key=%s; cooldown active",
                    entry.name,
                    observation.kind,
                    observation.dedupe_key,
                )
                self._state_store.set_delivery(entry.name, observation.dedupe_key, updated)
                return

        message = observation_to_input_message(
            observation,
            sensor_name=entry.name,
            thread_id=entry.thread_id,
            now=observation.occurred_at or now,
        )
        logger.info(
            "Enqueueing sensor event sensor=%s kind=%s severity=%s dedupe_key=%s event_id=%s summary=%s",
            entry.name,
            observation.kind,
            observation.severity,
            observation.dedupe_key,
            message.event_id,
            observation.summary[:200],
        )
        try:
            delivered = self._enqueue(message)
        except Exception:
            logger.exception(
                "Enqueue failed for sensor %s event_id=%s; not marking delivered",
                entry.name,
                message.event_id,
            )
            self._state_store.set_delivery(entry.name, observation.dedupe_key, updated)
            return

        if not delivered:
            logger.warning(
                "Sensor event not queued (queue full or not ready): sensor=%s event_id=%s kind=%s",
                entry.name,
                message.event_id,
                observation.kind,
            )
            self._state_store.set_delivery(entry.name, observation.dedupe_key, updated)
            return

        updated["last_delivered_at"] = now.isoformat()
        updated["last_event_id"] = message.event_id
        self._state_store.set_delivery(entry.name, observation.dedupe_key, updated)
        state.status.last_delivery_at = now


def _parse_optional_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        logger.warning("Ignoring unparsable delivery timestamp %r", value)
        return None
