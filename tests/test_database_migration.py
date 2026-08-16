from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import subprocess
import sys

from shellbot2.database import database_path
from shellbot2.message_history import MessageHistory
from shellbot2.sensorframework.state_store import SqliteSensorStateStore


MIGRATION_SCRIPT = Path(__file__).parents[1] / "scripts" / "migrate_conversation_sensor_db.py"


def _run_migration(datadir: Path, *extra_args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(MIGRATION_SCRIPT),
            "--datadir",
            str(datadir),
            *extra_args,
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def test_migration_merges_conversation_and_sensor_data(tmp_path):
    conversation_path = tmp_path / "message_history.db"
    sensor_path = tmp_path / "sensor_state.db"
    history = MessageHistory(conversation_path)
    interaction_id = history.add_interaction(
        "thread-1",
        [{"parts": [{"part_kind": "user-prompt", "content": "remember this"}]}],
    )
    history.set_active_thread_id("cli", "thread-1")
    history.engine.dispose()

    state_store = SqliteSensorStateStore(sensor_path)
    state_store.bind("disk_usage").set("last_sample", {"free_percent": 12})
    state_store.set_delivery(
        "disk_usage",
        "disk:/",
        {"last_delivered_at": "2026-01-01T00:00:00+00:00", "last_event_id": "event-1"},
    )
    state_store.record_observation(
        observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        sensor_name="disk_usage",
        kind="low_disk_space",
        severity="warning",
        summary="Disk is nearly full",
    )
    state_store.close()

    result = _run_migration(tmp_path)

    assert result.returncode == 0, result.stderr
    assert "messages: 1 rows" in result.stdout
    assert "sensor_state: 2 rows" in result.stdout
    assert "sensor_observations: 1 rows" in result.stdout
    target_path = database_path(tmp_path)
    assert target_path.exists()
    assert conversation_path.exists()
    assert sensor_path.exists()

    migrated_history = MessageHistory(target_path)
    assert migrated_history.get_active_thread_id("cli") == "thread-1"
    interactions = migrated_history.get_all_interactions("thread-1")
    assert interactions[0].interaction_id == interaction_id
    assert interactions[0].messages[0].message["parts"][0]["content"] == "remember this"
    migrated_history.engine.dispose()

    migrated_state_store = SqliteSensorStateStore(target_path)
    assert migrated_state_store.bind("disk_usage").get("last_sample") == {"free_percent": 12}
    assert migrated_state_store.get_delivery("disk_usage", "disk:/") == {
        "last_delivered_at": "2026-01-01T00:00:00+00:00",
        "last_event_id": "event-1",
    }
    observations = migrated_state_store._require_conn().execute(
        "SELECT sensor_name, kind, severity, summary FROM sensor_observations"
    ).fetchall()
    migrated_state_store.close()
    assert observations == [("disk_usage", "low_disk_space", "warning", "Disk is nearly full")]


def test_migration_supports_legacy_optional_tables_and_custom_sensor_path(tmp_path):
    conversation_path = tmp_path / "message_history.db"
    custom_sensor_path = tmp_path / "legacy" / "sensor.db"
    custom_sensor_path.parent.mkdir()

    with sqlite3.connect(conversation_path) as connection:
        connection.executescript(
            """
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY,
                thread_id TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at DATETIME NOT NULL
            );
            INSERT INTO messages (id, thread_id, message, created_at)
            VALUES (7, 'thread-7', '{"prompt": "legacy"}', '2026-01-01 00:00:00');
            """
        )
    with sqlite3.connect(custom_sensor_path) as connection:
        connection.executescript(
            """
            CREATE TABLE sensor_state (
                sensor_name TEXT NOT NULL,
                namespace TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                PRIMARY KEY (sensor_name, namespace, key)
            );
            INSERT INTO sensor_state (sensor_name, namespace, key, value)
            VALUES ('mail', 'plugin', 'cursor', '"2026-01-01T00:00:00"');
            """
        )

    result = _run_migration(tmp_path, "--sensor-db", str(custom_sensor_path))

    assert result.returncode == 0, result.stderr
    assert "active_client_threads: source table not present" in result.stdout
    assert "sensor_observations: source table not present" in result.stdout
    with sqlite3.connect(database_path(tmp_path)) as connection:
        assert connection.execute("SELECT id, interaction_id FROM messages").fetchall() == [(7, None)]
        assert connection.execute("SELECT COUNT(*) FROM active_client_threads").fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM sensor_observations").fetchone() == (0,)
        assert connection.execute("SELECT value FROM sensor_state").fetchone() == ('"2026-01-01T00:00:00"',)


def test_migration_refuses_to_replace_an_existing_destination(tmp_path):
    history = MessageHistory(tmp_path / "message_history.db")
    history.add_message("thread", {"prompt": "hello"})
    history.engine.dispose()
    target_path = database_path(tmp_path)
    target_path.write_bytes(b"existing destination")

    result = _run_migration(tmp_path)

    assert result.returncode == 1
    assert "Refusing to overwrite existing destination" in result.stderr
    assert target_path.read_bytes() == b"existing destination"


def test_migration_rejects_an_invalid_source_without_publishing_output(tmp_path):
    with sqlite3.connect(tmp_path / "message_history.db") as connection:
        connection.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, thread_id TEXT NOT NULL)")

    result = _run_migration(tmp_path)

    assert result.returncode == 1
    assert "missing required columns" in result.stderr
    assert not database_path(tmp_path).exists()


def test_migration_help_mentions_shutdown_requirement():
    result = subprocess.run(
        [sys.executable, str(MIGRATION_SCRIPT), "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "daemon and all clients" in result.stdout
