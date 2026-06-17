"""
Tests for the token usage tracking module.
"""

import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from shellbot2.usage_tracker import UsageTracker, format_usage_report


@pytest.fixture
def tracker(tmp_path):
    """Create a fresh UsageTracker with a temporary database."""
    db_path = tmp_path / "test_usage.db"
    return UsageTracker(db_path)


@pytest.fixture
def in_memory_tracker():
    """Create a UsageTracker backed by in-memory SQLite."""
    return UsageTracker(db_path=None)


class TestUsageTrackerRecord:
    """Tests for recording usage data."""

    def test_record_returns_id(self, tracker):
        record_id = tracker.record(
            thread_id="thread-1",
            model="gemini-flash",
            request_tokens=100,
            response_tokens=50,
            total_tokens=150,
        )
        assert isinstance(record_id, int)
        assert record_id > 0

    def test_record_multiple_entries(self, tracker):
        id1 = tracker.record("t1", "gemini-flash", 100, 50, 150)
        id2 = tracker.record("t1", "gemini-flash", 200, 100, 300)
        id3 = tracker.record("t2", "claude-sonnet", 300, 150, 450)
        assert id1 < id2 < id3

    def test_record_zero_tokens(self, tracker):
        record_id = tracker.record("t1", "model", 0, 0, 0)
        assert record_id > 0


class TestUsageTrackerTotalUsage:
    """Tests for get_total_usage aggregation."""

    def test_empty_database(self, tracker):
        total = tracker.get_total_usage()
        assert total["request_tokens"] == 0
        assert total["response_tokens"] == 0
        assert total["total_tokens"] == 0
        assert total["num_runs"] == 0

    def test_single_record(self, tracker):
        tracker.record("t1", "gemini", 100, 50, 150)
        total = tracker.get_total_usage()
        assert total["request_tokens"] == 100
        assert total["response_tokens"] == 50
        assert total["total_tokens"] == 150
        assert total["num_runs"] == 1

    def test_multiple_records_summed(self, tracker):
        tracker.record("t1", "gemini", 100, 50, 150)
        tracker.record("t1", "gemini", 200, 100, 300)
        tracker.record("t2", "claude", 300, 150, 450)
        total = tracker.get_total_usage()
        assert total["request_tokens"] == 600
        assert total["response_tokens"] == 300
        assert total["total_tokens"] == 900
        assert total["num_runs"] == 3

    def test_filter_by_thread(self, tracker):
        tracker.record("t1", "gemini", 100, 50, 150)
        tracker.record("t2", "gemini", 200, 100, 300)
        total = tracker.get_total_usage(thread_id="t1")
        assert total["total_tokens"] == 150
        assert total["num_runs"] == 1

    def test_filter_by_model(self, tracker):
        tracker.record("t1", "gemini", 100, 50, 150)
        tracker.record("t1", "claude", 200, 100, 300)
        total = tracker.get_total_usage(model="claude")
        assert total["total_tokens"] == 300
        assert total["num_runs"] == 1

    def test_filter_by_since(self, tracker):
        # Record something, then filter for future time
        tracker.record("t1", "gemini", 100, 50, 150)
        future = datetime.now() + timedelta(hours=1)
        total = tracker.get_total_usage(since=future)
        assert total["total_tokens"] == 0
        assert total["num_runs"] == 0

    def test_filter_by_since_includes_recent(self, tracker):
        tracker.record("t1", "gemini", 100, 50, 150)
        past = datetime.now() - timedelta(hours=1)
        total = tracker.get_total_usage(since=past)
        assert total["total_tokens"] == 150
        assert total["num_runs"] == 1


class TestUsageTrackerByModel:
    """Tests for get_usage_by_model aggregation."""

    def test_empty_database(self, tracker):
        result = tracker.get_usage_by_model()
        assert result == []

    def test_single_model(self, tracker):
        tracker.record("t1", "gemini", 100, 50, 150)
        tracker.record("t1", "gemini", 200, 100, 300)
        result = tracker.get_usage_by_model()
        assert len(result) == 1
        assert result[0]["model"] == "gemini"
        assert result[0]["total_tokens"] == 450
        assert result[0]["num_runs"] == 2

    def test_multiple_models_sorted(self, tracker):
        tracker.record("t1", "small-model", 10, 5, 15)
        tracker.record("t1", "big-model", 1000, 500, 1500)
        result = tracker.get_usage_by_model()
        assert len(result) == 2
        # Should be sorted by total_tokens descending
        assert result[0]["model"] == "big-model"
        assert result[1]["model"] == "small-model"


class TestUsageTrackerByThread:
    """Tests for get_usage_by_thread aggregation."""

    def test_empty_database(self, tracker):
        result = tracker.get_usage_by_thread()
        assert result == []

    def test_multiple_threads(self, tracker):
        tracker.record("thread-a", "gemini", 100, 50, 150)
        tracker.record("thread-b", "gemini", 500, 250, 750)
        tracker.record("thread-a", "gemini", 100, 50, 150)
        result = tracker.get_usage_by_thread()
        assert len(result) == 2
        # thread-b has more total tokens, should come first
        assert result[0]["thread_id"] == "thread-b"
        assert result[0]["total_tokens"] == 750
        assert result[1]["thread_id"] == "thread-a"
        assert result[1]["total_tokens"] == 300

    def test_limit(self, tracker):
        for i in range(20):
            tracker.record(f"thread-{i}", "gemini", 100, 50, 150)
        result = tracker.get_usage_by_thread(limit=5)
        assert len(result) == 5


class TestUsageTrackerRecentRecords:
    """Tests for get_recent_records."""

    def test_empty_database(self, tracker):
        result = tracker.get_recent_records()
        assert result == []

    def test_returns_recent_first(self, tracker):
        tracker.record("t1", "gemini", 100, 50, 150)
        tracker.record("t2", "claude", 200, 100, 300)
        result = tracker.get_recent_records(limit=10)
        assert len(result) == 2
        # Most recent first
        assert result[0]["model"] == "claude"
        assert result[1]["model"] == "gemini"

    def test_limit_respected(self, tracker):
        for i in range(10):
            tracker.record(f"t{i}", "gemini", 100, 50, 150)
        result = tracker.get_recent_records(limit=3)
        assert len(result) == 3


class TestUsageTrackerSummary:
    """Tests for get_summary."""

    def test_summary_structure(self, tracker):
        tracker.record("t1", "gemini", 100, 50, 150)
        summary = tracker.get_summary()
        assert "total" in summary
        assert "by_model" in summary
        assert "by_thread" in summary
        assert summary["total"]["num_runs"] == 1


class TestFormatUsageReport:
    """Tests for the format_usage_report function."""

    def test_empty_report(self, tracker):
        report = format_usage_report(tracker)
        assert "Token Usage Report" in report
        assert "All Time" in report

    def test_report_with_data(self, tracker):
        tracker.record("t1", "gemini-flash", 1000, 500, 1500)
        tracker.record("t1", "gemini-flash", 2000, 1000, 3000)
        tracker.record("t2", "claude-sonnet", 500, 250, 750)
        report = format_usage_report(tracker)
        assert "Token Usage Report" in report
        assert "gemini-flash" in report
        assert "claude-sonnet" in report

    def test_report_with_since(self, tracker):
        tracker.record("t1", "gemini", 100, 50, 150)
        since = datetime.now() - timedelta(hours=1)
        report = format_usage_report(tracker, since=since)
        assert "Since" in report


class TestInMemoryTracker:
    """Tests that the in-memory database works correctly."""

    def test_in_memory_basic(self, in_memory_tracker):
        in_memory_tracker.record("t1", "test-model", 50, 25, 75)
        total = in_memory_tracker.get_total_usage()
        assert total["total_tokens"] == 75

    def test_in_memory_isolation(self):
        """Two in-memory trackers should be independent."""
        tracker1 = UsageTracker(db_path=None)
        tracker2 = UsageTracker(db_path=None)
        tracker1.record("t1", "model", 100, 50, 150)
        total2 = tracker2.get_total_usage()
        assert total2["num_runs"] == 0


class TestPersistence:
    """Tests that data persists across tracker instances."""

    def test_data_persists(self, tmp_path):
        db_path = tmp_path / "persist_test.db"

        # Write with one tracker
        tracker1 = UsageTracker(db_path)
        tracker1.record("t1", "gemini", 100, 50, 150)

        # Read with a new tracker
        tracker2 = UsageTracker(db_path)
        total = tracker2.get_total_usage()
        assert total["total_tokens"] == 150
        assert total["num_runs"] == 1
