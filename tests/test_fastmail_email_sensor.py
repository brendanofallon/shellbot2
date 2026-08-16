import asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from shellbot2.sensorframework.discovery import discover_sensor_specs
from shellbot2.sensorframework.sensor_spec import SensorRuntime
from shellbot2.sensors.fastmail_sensor import (
    CURSOR_KEY,
    FastmailEmailSensor,
    SENSOR_SPECS,
)


class FakeFastmailClient:
    def __init__(self, emails: list[dict]) -> None:
        self.emails = emails
        self.calls: list[dict] = []

    def search_messages(self, **kwargs):
        self.calls.append(kwargs)
        since_dt = kwargs["since_dt"]
        limit = kwargs["limit"]
        matching = [
            email
            for email in self.emails
            if datetime.fromisoformat(email["receivedAt"].replace("Z", "+00:00")) > since_dt
        ]
        return sorted(matching, key=lambda email: email["receivedAt"])[:limit]


class MemoryState:
    def __init__(self) -> None:
        self._data: dict = {}

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def set(self, key: str, value) -> None:
        self._data[key] = value

    def delete(self, key: str) -> None:
        self._data.pop(key, None)


def _runtime(tmp_path: Path, state: MemoryState, config: dict | None = None) -> SensorRuntime:
    return SensorRuntime(
        datadir=tmp_path,
        sensor_name="fastmail_email",
        config=config or {},
        state=state,
        logger=MagicMock(),
        now=lambda: datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc),
    )


def _email(email_id: str, received_at: str, *, subject: str = "Status update") -> dict:
    return {
        "id": email_id,
        "from": [{"name": "Example Sender", "email": "sender@example.com"}],
        "subject": subject,
        "receivedAt": received_at,
        "preview": "The first part of this email.",
    }


def test_packaged_discovery_includes_fastmail_email():
    specs = discover_sensor_specs()
    assert "fastmail_email" in specs
    assert specs["fastmail_email"].default_interval_seconds == 300
    assert SENSOR_SPECS[0].name == "fastmail_email"


def test_bootstraps_then_reports_new_email_details(tmp_path):
    async def body():
        client = FakeFastmailClient(
            [_email("email-1", "2026-01-01T09:01:00Z", subject="Hello there")]
        )
        sensor = FastmailEmailSensor(client_factory=lambda: client)
        state = MemoryState()
        runtime = _runtime(tmp_path, state)

        assert await sensor.poll(runtime) == []
        assert state.get(CURSOR_KEY) == "2026-01-01T09:00:00+00:00"
        assert client.calls == []

        observations = await sensor.poll(runtime)

        assert len(observations) == 1
        observation = observations[0]
        assert observation.kind == "new_fastmail_email"
        assert observation.dedupe_key == "fastmail_email:email-1"
        assert observation.payload == {
            "id": "email-1",
            "sender": "Example Sender <sender@example.com>",
            "subject": "Hello there",
            "snippet": "The first part of this email.",
            "received_at": "2026-01-01T09:01:00+00:00",
        }
        assert "Example Sender <sender@example.com>" in observation.summary
        assert "Hello there" in observation.summary
        assert "The first part of this email." in observation.summary
        assert "2026-01-01T09:01:00+00:00" in observation.summary
        assert client.calls == [
            {
                "since_dt": datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc),
                "limit": 10,
                "ascending": True,
                "fetch_body_values": False,
            }
        ]
        assert state.get(CURSOR_KEY) == "2026-01-01T09:01:00+00:00"

    asyncio.run(body())


def test_max_messages_processes_oldest_messages_first(tmp_path):
    async def body():
        client = FakeFastmailClient(
            [
                _email("email-1", "2026-01-01T09:01:00Z"),
                _email("email-2", "2026-01-01T09:02:00Z"),
                _email("email-3", "2026-01-01T09:03:00Z"),
            ]
        )
        sensor = FastmailEmailSensor(client_factory=lambda: client)
        state = MemoryState()
        runtime = _runtime(tmp_path, state, {"max_messages": 2})

        await sensor.poll(runtime)
        first_batch = await sensor.poll(runtime)
        second_batch = await sensor.poll(runtime)

        assert [observation.payload["id"] for observation in first_batch] == [
            "email-1",
            "email-2",
        ]
        assert [observation.payload["id"] for observation in second_batch] == ["email-3"]
        assert state.get(CURSOR_KEY) == "2026-01-01T09:03:00+00:00"

    asyncio.run(body())


def test_missing_token_returns_no_observations(tmp_path):
    async def body():
        sensor = FastmailEmailSensor(
            client_factory=lambda: (_ for _ in ()).throw(ValueError("missing token"))
        )
        state = MemoryState()
        runtime = _runtime(tmp_path, state)
        state.set(CURSOR_KEY, "2026-01-01T09:00:00+00:00")

        assert await sensor.poll(runtime) == []
        runtime.logger.warning.assert_called_once_with(
            "fastmail_email requires FASTMAIL_API_TOKEN to be set"
        )

    asyncio.run(body())


def test_invalid_max_messages_does_not_call_fastmail(tmp_path):
    async def body():
        client = FakeFastmailClient([])
        sensor = FastmailEmailSensor(client_factory=lambda: client)
        state = MemoryState()
        runtime = _runtime(tmp_path, state, {"max_messages": 0})
        state.set(CURSOR_KEY, "2026-01-01T09:00:00+00:00")

        assert await sensor.poll(runtime) == []
        assert client.calls == []

    asyncio.run(body())
