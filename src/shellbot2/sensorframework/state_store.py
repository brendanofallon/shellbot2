"""Durable, namespaced sensor state backed by SQLite."""

from pathlib import Path
import json
import logging
import os
import sqlite3

from shellbot2.sensorframework.sensor_spec import (
    JSONValue,
    STATE_KEY_MAX_CHARS,
    validate_json_value,
    validate_sensor_name,
)


logger = logging.getLogger(__name__)

PLUGIN_NAMESPACE = "plugin"
FRAMEWORK_NAMESPACE = "framework"
DELIVERY_KEY_PREFIX = "delivery:"


def ensure_parent_dir(path: Path) -> None:
    """Create ``path``'s parent directory with restrictive permissions when new."""

    parent = path.parent
    created = not parent.exists()
    parent.mkdir(parents=True, exist_ok=True)
    if created and os.name != "nt":
        os.chmod(parent, 0o700)


class BoundSensorStateStore:
    """Sensor-facing view of :class:`SqliteSensorStateStore`.

    Writes are limited to the plugin namespace for a single sensor name.
    Framework-reserved keys are not visible or writable.
    """

    def __init__(self, store: "SqliteSensorStateStore", sensor_name: str) -> None:
        self._store = store
        self._sensor_name = validate_sensor_name(sensor_name, field_name="sensor_name")

    def get(self, key: str, default: JSONValue | None = None) -> JSONValue | None:
        return self._store._get(self._sensor_name, PLUGIN_NAMESPACE, key, default)

    def set(self, key: str, value: JSONValue) -> None:
        self._store._set(self._sensor_name, PLUGIN_NAMESPACE, key, value)

    def delete(self, key: str) -> None:
        self._store._delete(self._sensor_name, PLUGIN_NAMESPACE, key)


class SqliteSensorStateStore:
    """SQLite key-value store keyed by ``(sensor_name, namespace, key)``.

    Plugin state and framework delivery metadata share a database but not a
    namespace, so a plugin cannot overwrite cooldown or dedupe records.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        ensure_parent_dir(self.db_path)
        self._conn: sqlite3.Connection | None = sqlite3.connect(self.db_path, timeout=30)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sensor_state (
                sensor_name TEXT NOT NULL,
                namespace TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                PRIMARY KEY (sensor_name, namespace, key)
            )
            """
        )
        self._conn.commit()

    def bind(self, sensor_name: str) -> BoundSensorStateStore:
        """Return a plugin-facing store scoped to ``sensor_name``."""

        return BoundSensorStateStore(self, sensor_name)

    def close(self) -> None:
        """Close the database connection. Safe to call more than once."""

        if self._conn is None:
            return
        self._conn.close()
        self._conn = None

    def _require_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("sensor state store is closed")
        return self._conn

    def get_delivery(self, sensor_name: str, dedupe_key: str) -> dict | None:
        """Return framework delivery metadata for ``(sensor_name, dedupe_key)``."""

        value = self._get(
            sensor_name,
            FRAMEWORK_NAMESPACE,
            f"{DELIVERY_KEY_PREFIX}{dedupe_key}",
            default=None,
        )
        if value is None:
            return None
        if not isinstance(value, dict):
            logger.warning(
                "Ignoring malformed delivery state for sensor %r key %r",
                sensor_name,
                dedupe_key,
            )
            return None
        return value

    def set_delivery(self, sensor_name: str, dedupe_key: str, record: dict) -> None:
        """Persist framework delivery metadata for ``(sensor_name, dedupe_key)``."""

        self._set(
            sensor_name,
            FRAMEWORK_NAMESPACE,
            f"{DELIVERY_KEY_PREFIX}{dedupe_key}",
            record,
        )

    def _validate_key(self, key: str) -> str:
        if not isinstance(key, str) or not key.strip():
            raise ValueError("state key must be a non-empty string")
        if "\n" in key or "\r" in key:
            raise ValueError("state key must be a single line")
        if len(key) > STATE_KEY_MAX_CHARS:
            raise ValueError(f"state key must be at most {STATE_KEY_MAX_CHARS} characters")
        return key

    def _get(
        self,
        sensor_name: str,
        namespace: str,
        key: str,
        default: JSONValue | None,
    ) -> JSONValue | None:
        validate_sensor_name(sensor_name, field_name="sensor_name")
        key = self._validate_key(key)
        cursor = self._require_conn().execute(
            "SELECT value FROM sensor_state WHERE sensor_name = ? AND namespace = ? AND key = ?",
            (sensor_name, namespace, key),
        )
        row = cursor.fetchone()
        if row is None:
            return default
        try:
            return json.loads(row[0])
        except json.JSONDecodeError:
            logger.warning(
                "Ignoring corrupt JSON state for sensor %r namespace %r key %r",
                sensor_name,
                namespace,
                key,
            )
            return default

    def _set(self, sensor_name: str, namespace: str, key: str, value: JSONValue) -> None:
        validate_sensor_name(sensor_name, field_name="sensor_name")
        key = self._validate_key(key)
        serialized = json.dumps(validate_json_value(value, name="state value"), allow_nan=False)
        conn = self._require_conn()
        with conn:
            conn.execute(
                """
                INSERT INTO sensor_state (sensor_name, namespace, key, value)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(sensor_name, namespace, key) DO UPDATE SET value = excluded.value
                """,
                (sensor_name, namespace, key, serialized),
            )

    def _delete(self, sensor_name: str, namespace: str, key: str) -> None:
        validate_sensor_name(sensor_name, field_name="sensor_name")
        key = self._validate_key(key)
        conn = self._require_conn()
        with conn:
            conn.execute(
                "DELETE FROM sensor_state WHERE sensor_name = ? AND namespace = ? AND key = ?",
                (sensor_name, namespace, key),
            )
