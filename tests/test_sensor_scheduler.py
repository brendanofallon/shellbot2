import asyncio

from shellbot2.sensorframework.config import ResolvedSensorEntry
from shellbot2.sensorframework.scheduler import SensorScheduler
from shellbot2.sensorframework.sensor_spec import SensorSpec
from shellbot2.sensorframework.state_store import SqliteSensorStateStore
from tests.sensor_helpers import (
    FakeClock,
    RecordingSensor,
    SleepController,
    make_observation,
    make_spec,
    wait_until,
)


def _entry(spec, **overrides) -> ResolvedSensorEntry:
    values = {
        "name": spec.name,
        "spec": spec,
        "interval_seconds": 30,
        "cooldown_seconds": 900,
        "thread_id": f"sensor:{spec.name}",
        "config": {"flag": True},
    }
    values.update(overrides)
    return ResolvedSensorEntry(**values)


def _make_scheduler(tmp_path, entry, sensor, enqueue, clock=None, sleep=None, store=None):
    clock = clock or FakeClock()
    sleep = sleep or SleepController()
    store = store or SqliteSensorStateStore(tmp_path / "shellbot2.db")
    scheduler = SensorScheduler(
        [entry],
        datadir=tmp_path,
        state_store=store,
        enqueue=enqueue,
        clock=clock,
        sleep=sleep.sleep,
    )
    return scheduler, store, clock, sleep


def test_polls_immediately_then_after_interval(tmp_path):
    async def body():
        spec, sensor = make_spec("example_sensor")
        enqueued = []
        scheduler, store, clock, sleep = _make_scheduler(
            tmp_path,
            _entry(spec),
            sensor,
            enqueue=lambda message: enqueued.append(message) or True,
        )
        await scheduler.start()
        await wait_until(lambda: sensor.polls >= 1)
        assert sensor.polls == 1
        await sleep.wait_for_sleep()
        assert sleep.intervals == [30]
        sleep.release_one()
        await wait_until(lambda: sensor.polls >= 2)
        assert sensor.polls == 2
        await scheduler.stop()
        store.close()

    asyncio.run(body())


def test_polls_do_not_overlap(tmp_path):
    async def body():
        gate = asyncio.Event()
        entered = asyncio.Event()
        sensor = RecordingSensor(gate=gate, entered=entered)
        spec, _ = make_spec("example_sensor", sensor=sensor)
        scheduler, store, clock, sleep = _make_scheduler(
            tmp_path,
            _entry(spec, interval_seconds=1),
            sensor,
            enqueue=lambda message: True,
        )
        await scheduler.start()
        await asyncio.wait_for(entered.wait(), timeout=2)
        for _ in range(20):
            await asyncio.sleep(0)
        assert sensor.polls == 1
        assert sensor.max_concurrent == 1
        assert sleep.intervals == []
        gate.set()
        await scheduler.stop()
        store.close()

    asyncio.run(body())


def test_exception_during_poll_is_recovered(tmp_path):
    async def body():
        observation = make_observation()
        sensor = RecordingSensor(observations=[[], [observation]], error_on={1})
        spec, _ = make_spec("example_sensor", sensor=sensor)
        enqueued = []
        scheduler, store, clock, sleep = _make_scheduler(
            tmp_path,
            _entry(spec, cooldown_seconds=0),
            sensor,
            enqueue=lambda message: enqueued.append(message) or True,
        )
        await scheduler.start()
        await wait_until(lambda: sensor.polls >= 1 and scheduler.status()["example_sensor"].failure_count == 1)
        snapshot = scheduler.status()["example_sensor"]
        assert snapshot.failure_count == 1
        assert snapshot.last_failure_at is not None
        await sleep.wait_for_sleep()
        sleep.release_one()
        await wait_until(lambda: sensor.polls >= 2 and len(enqueued) == 1)
        assert len(enqueued) == 1
        assert scheduler.status()["example_sensor"].failure_count == 0
        await scheduler.stop()
        store.close()

    asyncio.run(body())


def test_cooldown_persists_across_scheduler_restart(tmp_path):
    async def body():
        observation = make_observation(dedupe_key="same")
        sensor = RecordingSensor(observations=[[observation]])
        spec, _ = make_spec("example_sensor", sensor=sensor)
        enqueued = []
        db_path = tmp_path / "shellbot2.db"
        store = SqliteSensorStateStore(db_path)
        clock = FakeClock()
        sleep = SleepController()
        scheduler, store, clock, sleep = _make_scheduler(
            tmp_path,
            _entry(spec, cooldown_seconds=900),
            sensor,
            enqueue=lambda message: enqueued.append(message) or True,
            clock=clock,
            sleep=sleep,
            store=store,
        )
        await scheduler.start()
        await wait_until(lambda: len(enqueued) == 1)
        await scheduler.stop()
        store.close()

        clock.advance(10)
        sensor2 = RecordingSensor(observations=[[observation]])
        spec2, _ = make_spec("example_sensor", sensor=sensor2)
        store2 = SqliteSensorStateStore(db_path)
        sleep2 = SleepController()
        scheduler2 = SensorScheduler(
            [_entry(spec2, cooldown_seconds=900)],
            datadir=tmp_path,
            state_store=store2,
            enqueue=lambda message: enqueued.append(message) or True,
            clock=clock,
            sleep=sleep2.sleep,
        )
        await scheduler2.start()
        await wait_until(lambda: sensor2.polls >= 1)
        assert len(enqueued) == 1
        await scheduler2.stop()
        store2.close()

    asyncio.run(body())


def test_dedupe_key_change_is_delivered_during_cooldown(tmp_path):
    async def body():
        first = make_observation(dedupe_key="a")
        second = make_observation(dedupe_key="b", summary="changed")
        sensor = RecordingSensor(observations=[[first], [second]])
        spec, _ = make_spec("example_sensor", sensor=sensor)
        enqueued = []
        scheduler, store, clock, sleep = _make_scheduler(
            tmp_path,
            _entry(spec, cooldown_seconds=900),
            sensor,
            enqueue=lambda message: enqueued.append(message) or True,
        )
        await scheduler.start()
        await wait_until(lambda: len(enqueued) == 1)
        await sleep.wait_for_sleep()
        sleep.release_one()
        await wait_until(lambda: len(enqueued) == 2)
        assert [message.metadata["dedupe_key"] for message in enqueued] == ["a", "b"]
        await scheduler.stop()
        store.close()

    asyncio.run(body())


def test_full_queue_does_not_mark_delivered(tmp_path):
    async def body():
        observation = make_observation()
        sensor = RecordingSensor(observations=[[observation], [observation]])
        spec, _ = make_spec("example_sensor", sensor=sensor)
        enqueued = []
        deliver = False

        def enqueue(message):
            if not deliver:
                return False
            enqueued.append(message)
            return True

        db_path = tmp_path / "shellbot2.db"
        store = SqliteSensorStateStore(db_path)
        scheduler, store, clock, sleep = _make_scheduler(
            tmp_path,
            _entry(spec, cooldown_seconds=900),
            sensor,
            enqueue=enqueue,
            store=store,
        )
        await scheduler.start()
        await wait_until(lambda: sensor.polls >= 1 and store.get_delivery("example_sensor", "key-1") is not None)
        record = store.get_delivery("example_sensor", "key-1")
        assert record is not None
        assert record["last_delivered_at"] is None
        assert enqueued == []

        deliver = True
        await sleep.wait_for_sleep()
        sleep.release_one()
        await wait_until(lambda: len(enqueued) == 1)
        assert len(enqueued) == 1
        record = store.get_delivery("example_sensor", "key-1")
        assert record["last_delivered_at"] is not None
        await scheduler.stop()
        store.close()

    asyncio.run(body())


def test_records_observations_even_when_cooldown_suppresses_delivery(tmp_path):
    async def body():
        observation = make_observation(summary="x" * 600)
        sensor = RecordingSensor(observations=[[observation], [observation]])
        spec, _ = make_spec("example_sensor", sensor=sensor)
        enqueued = []
        scheduler, store, clock, sleep = _make_scheduler(
            tmp_path,
            _entry(spec, cooldown_seconds=900),
            sensor,
            enqueue=lambda message: enqueued.append(message) or True,
        )
        await scheduler.start()
        await wait_until(lambda: len(enqueued) == 1)
        await sleep.wait_for_sleep()
        sleep.release_one()
        await wait_until(lambda: sensor.polls >= 2)

        rows = store._require_conn().execute(
            """
            SELECT observed_at, sensor_name, kind, severity, summary
            FROM sensor_observations
            ORDER BY id
            """
        ).fetchall()
        assert rows == [
            (clock().isoformat(), "example_sensor", "condition", "info", "x" * 500),
            (clock().isoformat(), "example_sensor", "condition", "info", "x" * 500),
        ]
        assert len(enqueued) == 1
        await scheduler.stop()
        store.close()

    asyncio.run(body())


def test_status_snapshot_includes_poll_and_delivery_fields(tmp_path):
    async def body():
        sensor = RecordingSensor(observations=[[make_observation()]])
        spec, _ = make_spec("example_sensor", sensor=sensor)
        scheduler, store, clock, sleep = _make_scheduler(
            tmp_path,
            _entry(spec),
            sensor,
            enqueue=lambda message: True,
        )
        await scheduler.start()
        await wait_until(lambda: scheduler.status()["example_sensor"].last_delivery_at is not None)
        snapshot = scheduler.status()["example_sensor"]
        assert snapshot.last_success_at is not None
        assert snapshot.last_delivery_at is not None
        assert snapshot.failure_count == 0
        assert snapshot.next_poll_time is not None
        await scheduler.stop()
        store.close()

    asyncio.run(body())


def test_factory_failure_does_not_stop_other_sensors(tmp_path):
    async def body():
        good_sensor = RecordingSensor(observations=[[make_observation()]])
        good_spec, _ = make_spec("good_sensor", sensor=good_sensor)

        def bad_factory(runtime):
            raise RuntimeError("factory boom")

        bad_spec = SensorSpec(
            name="bad_sensor",
            description="broken factory",
            factory=bad_factory,
        )
        enqueued: list = []
        store = SqliteSensorStateStore(tmp_path / "shellbot2.db")
        clock = FakeClock()
        sleep = SleepController()
        scheduler = SensorScheduler(
            [_entry(bad_spec), _entry(good_spec)],
            datadir=tmp_path,
            state_store=store,
            enqueue=lambda message: enqueued.append(message) or True,
            clock=clock,
            sleep=sleep.sleep,
        )
        await scheduler.start()
        await wait_until(lambda: len(enqueued) == 1)
        await wait_until(lambda: scheduler.status()["bad_sensor"].failure_count >= 1)
        assert scheduler.status()["good_sensor"].failure_count == 0
        assert enqueued[0].metadata["sensor_name"] == "good_sensor"
        await scheduler.stop()
        store.close()

    asyncio.run(body())
