from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from shellbot2.sensorframework.state_store import SqliteSensorStateStore


def test_bind_is_namespaced_and_persists(tmp_path):
    db_path = tmp_path / "shellbot2.db"
    store = SqliteSensorStateStore(db_path)
    alpha = store.bind("alpha")
    beta = store.bind("beta")

    alpha.set("cursor", {"id": 3})
    beta.set("cursor", {"id": 9})
    assert alpha.get("cursor") == {"id": 3}
    assert beta.get("cursor") == {"id": 9}

    alpha.delete("cursor")
    assert alpha.get("cursor", default="missing") == "missing"
    assert beta.get("cursor") == {"id": 9}
    store.close()

    restored = SqliteSensorStateStore(db_path)
    assert restored.bind("beta").get("cursor") == {"id": 9}
    restored.close()


def test_rejects_non_json_state_values(tmp_path):
    store = SqliteSensorStateStore(tmp_path / "shellbot2.db")
    bound = store.bind("alpha")
    with pytest.raises(ValueError, match="JSON"):
        bound.set("when", datetime.now(timezone.utc))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="JSON"):
        bound.set("items", {"a", "b"})  # type: ignore[arg-type]
    store.close()


def test_plugin_cannot_overwrite_framework_delivery_keys(tmp_path):
    store = SqliteSensorStateStore(tmp_path / "shellbot2.db")
    bound = store.bind("alpha")
    store.set_delivery("alpha", "k1", {"last_delivered_at": "t0", "last_event_id": "e1"})
    bound.set("delivery:k1", {"hijacked": True})
    bound.set("last_delivered_at", "nope")

    record = store.get_delivery("alpha", "k1")
    assert record["last_event_id"] == "e1"
    assert bound.get("delivery:k1") == {"hijacked": True}
    store.close()


def test_missing_key_returns_default(tmp_path):
    store = SqliteSensorStateStore(tmp_path / "shellbot2.db")
    bound = store.bind("alpha")
    assert bound.get("missing") is None
    assert bound.get("missing", default=0) == 0
    store.close()


def test_close_is_idempotent(tmp_path):
    store = SqliteSensorStateStore(tmp_path / "shellbot2.db")
    store.close()
    store.close()


def test_records_truncated_observation_history_and_prunes_oldest_rows(tmp_path):
    db_path = tmp_path / "shellbot2.db"
    store = SqliteSensorStateStore(db_path)
    observed_at = datetime(2026, 1, 1, tzinfo=timezone.utc)

    for index in range(1_001):
        store.record_observation(
            observed_at=observed_at + timedelta(seconds=index),
            sensor_name="alpha",
            kind="condition",
            severity="warning",
            summary=f"{index}-{'x' * 500}",
        )
    store.close()

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT observed_at, sensor_name, kind, severity, summary
            FROM sensor_observations
            ORDER BY id
            """
        ).fetchall()

    assert len(rows) == 1_000
    assert rows[0] == (
        (observed_at + timedelta(seconds=1)).isoformat(),
        "alpha",
        "condition",
        "warning",
        f"1-{'x' * 498}",
    )
    assert rows[-1] == (
        (observed_at + timedelta(seconds=1_000)).isoformat(),
        "alpha",
        "condition",
        "warning",
        f"1000-{'x' * 495}",
    )
