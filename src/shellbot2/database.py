"""Shared application database location."""

from pathlib import Path


DATABASE_FILENAME = "shellbot2.db"


def database_path(datadir: Path) -> Path:
    """Return the single SQLite database used for durable application data."""

    return Path(datadir) / DATABASE_FILENAME
