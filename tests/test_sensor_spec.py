import ast
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import shellbot2.sensorframework.sensor_spec as spec_mod
from shellbot2.sensorframework.sensor_spec import (
    PAYLOAD_MAX_BYTES,
    SUMMARY_MAX_CHARS,
    SensorObservation,
    SensorRuntime,
    SensorSpec,
    validate_json_value,
)


def test_sensor_spec_module_does_not_import_agent_zmq_or_daemon():
    source = Path(spec_mod.__file__).read_text()
    tree = ast.parse(source)
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module.split(".")[0])
            imported.append(node.module)
    assert "zmq" not in imported
    assert "shellbot2.agent" not in imported
    assert "shellbot2.daemon" not in imported
    assert "daemon" not in imported


def test_valid_spec_and_async_fake_sensor_do_not_need_agent(tmp_path):
    class FakeSensor:
        async def poll(self, runtime: SensorRuntime):
            runtime.state.set("seen", True)
            return [
                SensorObservation(
                    kind="tick",
                    summary="clock ticked",
                    dedupe_key="tick-1",
                    payload={"n": 1},
                )
            ]

    spec = SensorSpec(
        name="fake_clock",
        description="Emits a test tick",
        factory=lambda runtime: FakeSensor(),
    )
    assert spec.name == "fake_clock"
    assert spec.default_interval_seconds == 300
    assert callable(spec.factory)


@pytest.mark.parametrize("name", ["", "1bad", "has space", "bad/name", "a" * 65])
def test_invalid_sensor_spec_names(name):
    with pytest.raises(ValueError, match="name"):
        SensorSpec(name=name, description="ok", factory=lambda runtime: None)


@pytest.mark.parametrize("interval", [0, -1, True, 1.5, "300"])
def test_invalid_sensor_spec_intervals(interval):
    with pytest.raises(ValueError, match="default_interval_seconds"):
        SensorSpec(
            name="ok_sensor",
            description="ok",
            factory=lambda runtime: None,
            default_interval_seconds=interval,
        )


def test_spec_rejects_non_callable_factory():
    with pytest.raises(ValueError, match="factory"):
        SensorSpec(name="ok_sensor", description="ok", factory="not-callable")  # type: ignore[arg-type]


def test_spec_rejects_empty_description():
    with pytest.raises(ValueError, match="description"):
        SensorSpec(name="ok_sensor", description="  ", factory=lambda runtime: None)


def test_observation_rejects_empty_kind_and_summary():
    with pytest.raises(ValueError, match="kind"):
        SensorObservation(kind="", summary="ok", dedupe_key="k")
    with pytest.raises(ValueError, match="summary"):
        SensorObservation(kind="k", summary="", dedupe_key="k")
    with pytest.raises(ValueError, match="dedupe_key"):
        SensorObservation(kind="k", summary="ok", dedupe_key="")


def test_observation_rejects_multiline_kind():
    with pytest.raises(ValueError, match="single line"):
        SensorObservation(kind="a\nb", summary="ok", dedupe_key="k")


def test_observation_rejects_oversized_summary():
    with pytest.raises(ValueError, match="summary"):
        SensorObservation(
            kind="k",
            summary="x" * (SUMMARY_MAX_CHARS + 1),
            dedupe_key="k",
        )


def test_observation_rejects_non_mapping_payload():
    with pytest.raises(ValueError, match="payload"):
        SensorObservation(kind="k", summary="ok", dedupe_key="k", payload=["x"])  # type: ignore[arg-type]


def test_observation_rejects_non_json_payload():
    with pytest.raises(ValueError, match="JSON"):
        SensorObservation(
            kind="k",
            summary="ok",
            dedupe_key="k",
            payload={"when": datetime.now(timezone.utc)},
        )


def test_observation_rejects_oversized_payload():
    with pytest.raises(ValueError, match="size limit"):
        SensorObservation(
            kind="k",
            summary="ok",
            dedupe_key="k",
            payload={"blob": "x" * PAYLOAD_MAX_BYTES},
        )


def test_observation_rejects_invalid_severity():
    with pytest.raises(ValueError, match="severity"):
        SensorObservation(kind="k", summary="ok", dedupe_key="k", severity="fatal")  # type: ignore[arg-type]


def test_validate_json_value_rejects_nan_and_sets():
    with pytest.raises(ValueError):
        validate_json_value(float("nan"))
    with pytest.raises(ValueError):
        validate_json_value({"x", "y"})
    assert validate_json_value({"a": [1, True, None]}) == {"a": [1, True, None]}


def test_valid_observation_accepts_json_payload():
    observation = SensorObservation(
        kind="disk_low",
        summary="Free space is low",
        dedupe_key="disk:/:10pct",
        payload={"path": "/", "percent_free": 10},
        severity="warning",
    )
    assert observation.kind == "disk_low"
    json.dumps(dict(observation.payload), allow_nan=False)
