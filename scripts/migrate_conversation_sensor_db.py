#!/usr/bin/env python3
"""Merge legacy conversation and sensor SQLite databases into ``shellbot2.db``.

Stop the ShellBot daemon and all clients using the data directory before running
this command. The legacy database files are read-only inputs and are never
modified or deleted by this script.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable
from dataclasses import dataclass
import os
from pathlib import Path
import sqlite3
import sys
import tempfile


class MigrationError(Exception):
    """Raised when a legacy database cannot be safely migrated."""


@dataclass(frozen=True)
class MigrationResult:
    """Summary of data copied to the unified database."""

    output_path: Path
    copied_rows: dict[str, int]
    skipped_tables: tuple[str, ...]


TARGET_SCHEMA = (
    """
    CREATE TABLE messages (
        id INTEGER PRIMARY KEY,
        thread_id TEXT NOT NULL,
        interaction_id TEXT,
        message TEXT NOT NULL,
        created_at DATETIME NOT NULL
    )
    """,
    "CREATE INDEX ix_messages_thread_id ON messages (thread_id)",
    "CREATE INDEX ix_messages_interaction_id ON messages (interaction_id)",
    """
    CREATE TABLE active_client_threads (
        client_id TEXT PRIMARY KEY,
        thread_id TEXT NOT NULL,
        updated_at DATETIME NOT NULL
    )
    """,
    """
    CREATE TABLE sensor_state (
        sensor_name TEXT NOT NULL,
        namespace TEXT NOT NULL,
        key TEXT NOT NULL,
        value TEXT NOT NULL,
        PRIMARY KEY (sensor_name, namespace, key)
    )
    """,
    """
    CREATE TABLE sensor_observations (
        id INTEGER PRIMARY KEY,
        observed_at TEXT NOT NULL,
        sensor_name TEXT NOT NULL,
        kind TEXT NOT NULL,
        severity TEXT NOT NULL CHECK (severity IN ('info', 'warning', 'critical')),
        summary TEXT NOT NULL CHECK (length(summary) <= 500)
    )
    """,
)


def _read_only_connection(path: Path) -> sqlite3.Connection:
    """Open a source database read-only, including committed WAL contents."""

    try:
        connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise MigrationError(f"Could not open {path}: {exc}") from exc
    connection.execute("PRAGMA query_only = ON")
    _verify_integrity(connection, path)
    return connection


def _verify_integrity(connection: sqlite3.Connection, path: Path) -> None:
    integrity_result = connection.execute("PRAGMA integrity_check").fetchone()
    if integrity_result != ("ok",):
        raise MigrationError(f"Database integrity check failed for {path}: {integrity_result!r}")
    foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
    if foreign_key_errors:
        raise MigrationError(f"Foreign key check failed for {path}: {foreign_key_errors!r}")


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()
    return row is not None


def _require_columns(
    connection: sqlite3.Connection,
    *,
    database_path: Path,
    table: str,
    required: Iterable[str],
) -> set[str]:
    if not _table_exists(connection, table):
        raise MigrationError(f"Expected table {table!r} is missing from {database_path}")
    columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
    missing = set(required) - columns
    if missing:
        missing_columns = ", ".join(sorted(missing))
        raise MigrationError(
            f"Table {table!r} in {database_path} is missing required columns: {missing_columns}"
        )
    return columns


def _copy_rows(
    target: sqlite3.Connection,
    source: sqlite3.Connection,
    *,
    source_query: str,
    insert_query: str,
    target_table: str,
) -> int:
    expected_count = source.execute(f"SELECT COUNT(*) FROM ({source_query})").fetchone()[0]
    target.executemany(insert_query, source.execute(source_query))
    actual_count = target.execute(f"SELECT COUNT(*) FROM {target_table}").fetchone()[0]
    if actual_count != expected_count:
        raise MigrationError(
            f"Copied {actual_count} rows to {target_table}, expected {expected_count}"
        )
    return actual_count


def _create_target_schema(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = DELETE")
    for statement in TARGET_SCHEMA:
        connection.execute(statement)


def _migrate_to_staging(
    *,
    conversation_path: Path,
    sensor_path: Path | None,
    staging_path: Path,
) -> tuple[dict[str, int], tuple[str, ...]]:
    conversation = _read_only_connection(conversation_path)
    sensor = _read_only_connection(sensor_path) if sensor_path is not None else None
    target = sqlite3.connect(staging_path)
    copied_rows: dict[str, int] = {}
    skipped_tables: list[str] = []

    try:
        message_columns = _require_columns(
            conversation,
            database_path=conversation_path,
            table="messages",
            required=("id", "thread_id", "message", "created_at"),
        )
        has_active_threads = _table_exists(conversation, "active_client_threads")
        if has_active_threads:
            _require_columns(
                conversation,
                database_path=conversation_path,
                table="active_client_threads",
                required=("client_id", "thread_id", "updated_at"),
            )
        else:
            skipped_tables.append("active_client_threads")

        if sensor is not None:
            _require_columns(
                sensor,
                database_path=sensor_path,
                table="sensor_state",
                required=("sensor_name", "namespace", "key", "value"),
            )
            has_observations = _table_exists(sensor, "sensor_observations")
            if has_observations:
                _require_columns(
                    sensor,
                    database_path=sensor_path,
                    table="sensor_observations",
                    required=("id", "observed_at", "sensor_name", "kind", "severity", "summary"),
                )
            else:
                skipped_tables.append("sensor_observations")
        else:
            has_observations = False
            skipped_tables.extend(("sensor_state", "sensor_observations"))

        _create_target_schema(target)
        with target:
            interaction_id = "interaction_id" if "interaction_id" in message_columns else "NULL"
            copied_rows["messages"] = _copy_rows(
                target,
                conversation,
                source_query=(
                    "SELECT id, thread_id, "
                    f"{interaction_id}, message, created_at FROM messages ORDER BY id"
                ),
                insert_query=(
                    "INSERT INTO messages (id, thread_id, interaction_id, message, created_at) "
                    "VALUES (?, ?, ?, ?, ?)"
                ),
                target_table="messages",
            )
            if has_active_threads:
                copied_rows["active_client_threads"] = _copy_rows(
                    target,
                    conversation,
                    source_query=(
                        "SELECT client_id, thread_id, updated_at FROM active_client_threads "
                        "ORDER BY client_id"
                    ),
                    insert_query=(
                        "INSERT INTO active_client_threads (client_id, thread_id, updated_at) "
                        "VALUES (?, ?, ?)"
                    ),
                    target_table="active_client_threads",
                )
            else:
                copied_rows["active_client_threads"] = 0

            if sensor is not None:
                copied_rows["sensor_state"] = _copy_rows(
                    target,
                    sensor,
                    source_query=(
                        "SELECT sensor_name, namespace, key, value FROM sensor_state "
                        "ORDER BY sensor_name, namespace, key"
                    ),
                    insert_query=(
                        "INSERT INTO sensor_state (sensor_name, namespace, key, value) "
                        "VALUES (?, ?, ?, ?)"
                    ),
                    target_table="sensor_state",
                )
                if has_observations:
                    copied_rows["sensor_observations"] = _copy_rows(
                        target,
                        sensor,
                        source_query=(
                            "SELECT id, observed_at, sensor_name, kind, severity, summary "
                            "FROM sensor_observations ORDER BY id"
                        ),
                        insert_query=(
                            "INSERT INTO sensor_observations "
                            "(id, observed_at, sensor_name, kind, severity, summary) "
                            "VALUES (?, ?, ?, ?, ?, ?)"
                        ),
                        target_table="sensor_observations",
                    )
                else:
                    copied_rows["sensor_observations"] = 0
            else:
                copied_rows["sensor_state"] = 0
                copied_rows["sensor_observations"] = 0

            _verify_integrity(target, staging_path)
    except sqlite3.Error as exc:
        raise MigrationError(f"Could not migrate legacy databases: {exc}") from exc
    finally:
        target.close()
        conversation.close()
        if sensor is not None:
            sensor.close()

    return copied_rows, tuple(skipped_tables)


def migrate(
    *,
    conversation_path: Path,
    sensor_path: Path | None,
    output_path: Path,
) -> MigrationResult:
    """Create ``output_path`` by merging the supplied legacy databases."""

    conversation_path = conversation_path.expanduser().resolve()
    sensor_path = sensor_path.expanduser().resolve() if sensor_path is not None else None
    output_path = output_path.expanduser().resolve()

    if not conversation_path.is_file():
        raise MigrationError(f"Conversation database does not exist: {conversation_path}")
    if sensor_path is not None and not sensor_path.is_file():
        raise MigrationError(f"Sensor database does not exist: {sensor_path}")
    if output_path.exists():
        raise MigrationError(f"Refusing to overwrite existing destination: {output_path}")
    if output_path == conversation_path or output_path == sensor_path:
        raise MigrationError("Destination database must differ from every source database")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=".shellbot2-migration-",
        suffix=".db",
        dir=output_path.parent,
    )
    os.close(file_descriptor)
    staging_path = Path(temporary_name)
    os.chmod(staging_path, 0o600)

    try:
        copied_rows, skipped_tables = _migrate_to_staging(
            conversation_path=conversation_path,
            sensor_path=sensor_path,
            staging_path=staging_path,
        )
        if output_path.exists():
            raise MigrationError(f"Refusing to overwrite existing destination: {output_path}")
        os.replace(staging_path, output_path)
    except Exception:
        staging_path.unlink(missing_ok=True)
        raise

    return MigrationResult(
        output_path=output_path,
        copied_rows=copied_rows,
        skipped_tables=skipped_tables,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Merge legacy conversation and sensor SQLite databases into shellbot2.db. "
            "Stop the daemon and all clients before running this command."
        )
    )
    parser.add_argument(
        "--datadir",
        type=Path,
        default=Path("~/.shellbot2"),
        help="Data directory containing legacy databases (default: ~/.shellbot2)",
    )
    parser.add_argument(
        "--conversation-db",
        type=Path,
        help="Legacy conversation database (default: <datadir>/message_history.db)",
    )
    parser.add_argument(
        "--sensor-db",
        type=Path,
        help="Legacy sensor database (default: <datadir>/sensor_state.db when present)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Unified database output (default: <datadir>/shellbot2.db)",
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    datadir = args.datadir.expanduser()
    conversation_path = args.conversation_db or datadir / "message_history.db"
    default_sensor_path = datadir / "sensor_state.db"
    sensor_path = args.sensor_db or (default_sensor_path if default_sensor_path.exists() else None)
    output_path = args.output or datadir / "shellbot2.db"

    try:
        result = migrate(
            conversation_path=conversation_path,
            sensor_path=sensor_path,
            output_path=output_path,
        )
    except MigrationError as exc:
        print(f"Migration failed: {exc}", file=sys.stderr)
        return 1

    print(f"Created unified database: {result.output_path}")
    for table, count in result.copied_rows.items():
        print(f"  {table}: {count} rows")
    for table in result.skipped_tables:
        print(f"  {table}: source table not present")
    print("Legacy database files were not modified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
