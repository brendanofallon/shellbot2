"""
Token usage tracking module for ShellBot2.

Provides persistent tracking of token usage per thread, per model, and over time
using SQLite. Includes reporting capabilities for cost monitoring.
"""

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from sqlalchemy import Column, DateTime, Float, Integer, String, create_engine, func
from sqlalchemy.orm import DeclarativeBase, sessionmaker

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """Base class for SQLAlchemy models."""
    pass


class UsageRecord(Base):
    """
    SQLAlchemy model for storing token usage records.

    Each record captures the token counts for a single agent run,
    along with metadata about the model, thread, and timing.
    """
    __tablename__ = "usage_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    thread_id = Column(String, nullable=False, index=True)
    model = Column(String, nullable=False, index=True)
    request_tokens = Column(Integer, nullable=False, default=0)
    response_tokens = Column(Integer, nullable=False, default=0)
    total_tokens = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    def __repr__(self) -> str:
        return (
            f"<UsageRecord(id={self.id}, model={self.model}, "
            f"total_tokens={self.total_tokens}, created_at={self.created_at})>"
        )


class UsageTracker:
    """
    Persistent token usage tracker backed by SQLite.

    Records token usage for every agent run and provides aggregation
    queries for reporting (per thread, per model, over time windows).
    """

    def __init__(self, db_path: Optional[str | Path] = None):
        """
        Initialize the UsageTracker.

        Args:
            db_path: Path to the SQLite database file.
                     If None, creates an in-memory database (useful for testing).
        """
        if db_path is None:
            db_url = "sqlite:///:memory:"
        else:
            db_path = Path(db_path)
            db_url = f"sqlite:///{db_path}"

        self.engine = create_engine(db_url)
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine)
        logger.info(f"UsageTracker initialized with db: {db_url}")

    def record(
        self,
        thread_id: str,
        model: str,
        request_tokens: int,
        response_tokens: int,
        total_tokens: int,
    ) -> int:
        """
        Record a single usage event.

        Args:
            thread_id: The conversation thread ID.
            model: The model name/identifier used for this run.
            request_tokens: Number of input/prompt tokens.
            response_tokens: Number of output/completion tokens.
            total_tokens: Total tokens (request + response).

        Returns:
            The ID of the newly created usage record.
        """
        with self.SessionLocal() as session:
            record = UsageRecord(
                thread_id=thread_id,
                model=model,
                request_tokens=request_tokens,
                response_tokens=response_tokens,
                total_tokens=total_tokens,
                created_at=datetime.now(),
            )
            session.add(record)
            session.commit()
            session.refresh(record)
            logger.info(
                f"Recorded usage: model={model}, "
                f"request={request_tokens}, response={response_tokens}, "
                f"total={total_tokens}"
            )
            return record.id

    def get_total_usage(
        self,
        since: Optional[datetime] = None,
        thread_id: Optional[str] = None,
        model: Optional[str] = None,
    ) -> dict:
        """
        Get aggregated total usage, optionally filtered by time, thread, or model.

        Args:
            since: Only include records created after this datetime.
            thread_id: Filter by thread ID.
            model: Filter by model name.

        Returns:
            Dictionary with keys: request_tokens, response_tokens, total_tokens, num_runs.
        """
        with self.SessionLocal() as session:
            query = session.query(
                func.coalesce(func.sum(UsageRecord.request_tokens), 0).label("request_tokens"),
                func.coalesce(func.sum(UsageRecord.response_tokens), 0).label("response_tokens"),
                func.coalesce(func.sum(UsageRecord.total_tokens), 0).label("total_tokens"),
                func.count(UsageRecord.id).label("num_runs"),
            )
            if since is not None:
                query = query.filter(UsageRecord.created_at >= since)
            if thread_id is not None:
                query = query.filter(UsageRecord.thread_id == thread_id)
            if model is not None:
                query = query.filter(UsageRecord.model == model)

            row = query.one()
            return {
                "request_tokens": row.request_tokens,
                "response_tokens": row.response_tokens,
                "total_tokens": row.total_tokens,
                "num_runs": row.num_runs,
            }

    def get_usage_by_model(
        self,
        since: Optional[datetime] = None,
    ) -> list[dict]:
        """
        Get usage aggregated by model.

        Args:
            since: Only include records created after this datetime.

        Returns:
            List of dicts, each with: model, request_tokens, response_tokens,
            total_tokens, num_runs. Sorted by total_tokens descending.
        """
        with self.SessionLocal() as session:
            query = session.query(
                UsageRecord.model,
                func.coalesce(func.sum(UsageRecord.request_tokens), 0).label("request_tokens"),
                func.coalesce(func.sum(UsageRecord.response_tokens), 0).label("response_tokens"),
                func.coalesce(func.sum(UsageRecord.total_tokens), 0).label("total_tokens"),
                func.count(UsageRecord.id).label("num_runs"),
            ).group_by(UsageRecord.model)

            if since is not None:
                query = query.filter(UsageRecord.created_at >= since)

            query = query.order_by(func.sum(UsageRecord.total_tokens).desc())
            rows = query.all()
            return [
                {
                    "model": row.model,
                    "request_tokens": row.request_tokens,
                    "response_tokens": row.response_tokens,
                    "total_tokens": row.total_tokens,
                    "num_runs": row.num_runs,
                }
                for row in rows
            ]

    def get_usage_by_thread(
        self,
        since: Optional[datetime] = None,
        limit: int = 10,
    ) -> list[dict]:
        """
        Get usage aggregated by thread, showing the top threads by total tokens.

        Args:
            since: Only include records created after this datetime.
            limit: Maximum number of threads to return.

        Returns:
            List of dicts with: thread_id, request_tokens, response_tokens,
            total_tokens, num_runs. Sorted by total_tokens descending.
        """
        with self.SessionLocal() as session:
            query = session.query(
                UsageRecord.thread_id,
                func.coalesce(func.sum(UsageRecord.request_tokens), 0).label("request_tokens"),
                func.coalesce(func.sum(UsageRecord.response_tokens), 0).label("response_tokens"),
                func.coalesce(func.sum(UsageRecord.total_tokens), 0).label("total_tokens"),
                func.count(UsageRecord.id).label("num_runs"),
            ).group_by(UsageRecord.thread_id)

            if since is not None:
                query = query.filter(UsageRecord.created_at >= since)

            query = query.order_by(func.sum(UsageRecord.total_tokens).desc())
            query = query.limit(limit)
            rows = query.all()
            return [
                {
                    "thread_id": row.thread_id,
                    "request_tokens": row.request_tokens,
                    "response_tokens": row.response_tokens,
                    "total_tokens": row.total_tokens,
                    "num_runs": row.num_runs,
                }
                for row in rows
            ]

    def get_recent_records(self, limit: int = 20) -> list[dict]:
        """
        Get the most recent usage records.

        Args:
            limit: Maximum number of records to return.

        Returns:
            List of dicts with full record details, most recent first.
        """
        with self.SessionLocal() as session:
            records = (
                session.query(UsageRecord)
                .order_by(UsageRecord.created_at.desc())
                .limit(limit)
                .all()
            )
            return [
                {
                    "id": r.id,
                    "thread_id": r.thread_id,
                    "model": r.model,
                    "request_tokens": r.request_tokens,
                    "response_tokens": r.response_tokens,
                    "total_tokens": r.total_tokens,
                    "created_at": r.created_at.isoformat(),
                }
                for r in records
            ]

    def get_summary(self, since: Optional[datetime] = None) -> dict:
        """
        Get a comprehensive usage summary.

        Args:
            since: Only include records created after this datetime.

        Returns:
            Dictionary with total usage, per-model breakdown, and top threads.
        """
        return {
            "total": self.get_total_usage(since=since),
            "by_model": self.get_usage_by_model(since=since),
            "by_thread": self.get_usage_by_thread(since=since, limit=5),
        }


def format_usage_report(tracker: UsageTracker, since: Optional[datetime] = None) -> str:
    """
    Generate a human-readable usage report.

    Args:
        tracker: The UsageTracker instance to query.
        since: Only include records created after this datetime.

    Returns:
        Formatted string report suitable for terminal display.
    """
    summary = tracker.get_summary(since=since)
    total = summary["total"]
    by_model = summary["by_model"]
    by_thread = summary["by_thread"]

    lines = []
    time_label = "All Time" if since is None else f"Since {since.strftime('%Y-%m-%d %H:%M')}"
    lines.append(f"\n  📊 Token Usage Report ({time_label})")
    lines.append(f"  {'=' * 50}")

    # Overall totals
    lines.append(f"\n  Total Runs:       {total['num_runs']:>10,}")
    lines.append(f"  Request Tokens:   {total['request_tokens']:>10,}")
    lines.append(f"  Response Tokens:  {total['response_tokens']:>10,}")
    lines.append(f"  Total Tokens:     {total['total_tokens']:>10,}")

    # By model
    if by_model:
        lines.append(f"\n  📦 Usage by Model")
        lines.append(f"  {'-' * 50}")
        for entry in by_model:
            lines.append(
                f"  {entry['model']:<35} "
                f"{entry['total_tokens']:>10,} tokens "
                f"({entry['num_runs']} runs)"
            )

    # By thread
    if by_thread:
        lines.append(f"\n  🧵 Top Threads by Usage")
        lines.append(f"  {'-' * 50}")
        for entry in by_thread:
            short_id = entry["thread_id"][:8] + "..."
            lines.append(
                f"  {short_id:<15} "
                f"{entry['total_tokens']:>10,} tokens "
                f"({entry['num_runs']} runs)"
            )

    lines.append("")
    return "\n".join(lines)
