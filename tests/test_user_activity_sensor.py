import asyncio
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from unittest.mock import MagicMock, patch
import os
import zipfile

import pytest

from shellbot2.sensorframework.discovery import discover_sensor_specs
from shellbot2.sensorframework.sensor_spec import SensorObservation, SensorRuntime
from shellbot2.sensors.user_activity import (
    ACTIVITY_KIND,
    FileActivityTools,
    UserActivitySensor,
    _create_activity_agent,
)


class MemoryState:
    def __init__(self) -> None:
        self.data: dict = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value) -> None:
        self.data[key] = value

    def delete(self, key) -> None:
        self.data.pop(key, None)


def _runtime(tmp_path: Path, config: dict | None = None) -> SensorRuntime:
    return SensorRuntime(
        datadir=tmp_path,
        sensor_name="user_activity",
        config=config or {},
        state=MemoryState(),
        logger=MagicMock(),
        now=lambda: datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc),
    )


def test_list_recent_files_returns_newest_regular_files_only(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    older = home / "older.txt"
    newer = home / "nested" / "newer.md"
    newer.parent.mkdir()
    older.write_text("older")
    newer.write_text("newer")
    os.utime(older, (100, 100))
    os.utime(newer, (200, 200))
    (home / "directory").mkdir()

    results = FileActivityTools(home).list_recent_files(n=2)

    assert results == [
        {
            "path": str(newer),
            "modified_at": "1970-01-01T00:03:20+00:00",
        },
        {
            "path": str(older),
            "modified_at": "1970-01-01T00:01:40+00:00",
        },
    ]


def test_read_file_returns_bounded_utf8_text_inside_untrusted_markers(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    source = home / "notes.txt"
    source.write_text("working notes\n" + ("x" * 30_000))

    result = FileActivityTools(home).read_file(str(source))

    assert result["path"] == str(source)
    assert result["file_type"] == "text"
    assert result["content_truncated"] is True
    assert result["content"].startswith("----- BEGIN UNTRUSTED FILE CONTENT -----\n")
    assert "working notes" in result["content"]
    assert result["content"].endswith("----- END UNTRUSTED FILE CONTENT -----")


def test_read_file_rejects_paths_outside_home_directory(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("private")

    with pytest.raises(ValueError, match="inside the user's home directory"):
        FileActivityTools(home).read_file(str(outside))


def test_read_file_extracts_basic_docx_text(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    document = home / "plan.docx"
    document_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>Implement the activity sensor</w:t></w:r></w:p>
    <w:p><w:r><w:t>Write tests</w:t></w:r></w:p>
  </w:body>
</w:document>
"""
    with zipfile.ZipFile(document, "w") as archive:
        archive.writestr("word/document.xml", document_xml)

    result = FileActivityTools(home).read_file(str(document))

    assert result["file_type"] == "docx"
    assert "Implement the activity sensor\nWrite tests" in result["content"]


def test_read_file_extracts_pdf_through_pymupdf(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    document = home / "notes.pdf"
    document.write_bytes(b"%PDF-1.7")
    monkeypatch.setattr(
        "shellbot2.sensors.user_activity.pymupdf4llm.to_markdown",
        lambda path: f"extracted from {path.name}",
    )

    result = FileActivityTools(home).read_file(str(document))

    assert result["file_type"] == "pdf"
    assert "extracted from notes.pdf" in result["content"]


class FakeRunResult:
    def __init__(self, output: SensorObservation) -> None:
        self.output = output


class FakeActivityAgent:
    def __init__(self, output: SensorObservation) -> None:
        self.output = output
        self.prompts: list[str] = []

    def run_sync(self, prompt: str) -> FakeRunResult:
        self.prompts.append(prompt)
        return FakeRunResult(self.output)


def test_sensor_delivers_one_normalized_activity_observation(tmp_path):
    async def poll() -> None:
        output = SensorObservation(
            kind="anything",
            summary="The user appears to be implementing a local sensor.",
            dedupe_key="model-chosen-key",
            payload={"evidence_files": ["~/src/project/sensor.py"], "uncertain": True},
            severity="warning",
        )
        agent = FakeActivityAgent(output)
        sensor = UserActivitySensor(
            home_dir=tmp_path,
            agent_factory=lambda _tools: agent,
        )

        observations = await sensor.poll(_runtime(tmp_path, {"recent_file_count": 12}))

        assert len(observations) == 1
        observation = observations[0]
        assert observation.kind == ACTIVITY_KIND
        assert observation.summary == output.summary
        assert observation.dedupe_key == (
            f"user_activity:{sha256(output.summary.encode()).hexdigest()}"
        )
        assert observation.payload == output.payload
        assert observation.occurred_at == datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)
        assert observation.severity == "info"
        assert "list_recent_files with n=12" in agent.prompts[0]

    asyncio.run(poll())


def test_sensor_skips_invalid_configuration_without_calling_llm(tmp_path):
    async def poll() -> None:
        agent = FakeActivityAgent(
            SensorObservation(
                kind=ACTIVITY_KIND,
                summary="unused",
                dedupe_key="unused",
            )
        )
        sensor = UserActivitySensor(
            home_dir=tmp_path,
            agent_factory=lambda _tools: agent,
        )
        runtime = _runtime(tmp_path, {"recent_file_count": 0})

        assert await sensor.poll(runtime) == []
        assert agent.prompts == []
        runtime.logger.warning.assert_called_once()

    asyncio.run(poll())


def test_packaged_discovery_includes_user_activity():
    specs = discover_sensor_specs()

    assert specs["user_activity"].default_interval_seconds == 1800


def test_activity_agent_uses_foundry_env_vars_without_azure_openai_key(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    with patch.dict(
        os.environ,
        {
            "AZURE_FOUNDRY_ENDPOINT": (
                "https://resource-openai-sandbox-eastus2.services.ai.azure.com/openai/v1/"
            ),
            "AZURE_FOUNDRY_API_KEY": "foundry-key",
            "AZURE_OPENAI_API_KEY": "",
            "AZURE_OPENAI_ENDPOINT": "",
        },
        clear=False,
    ):
        agent = _create_activity_agent(FileActivityTools(home))

    assert agent.model.model_name == "gpt-5.6-luna"
    assert agent.model.system == "openai"
