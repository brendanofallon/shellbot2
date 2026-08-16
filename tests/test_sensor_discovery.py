import logging
from types import ModuleType

from shellbot2.sensorframework.discovery import _discover_from_modules, discover_sensor_specs
from shellbot2.sensorframework.sensor_spec import SensorSpec
from tests.sensor_helpers import make_spec


CUSTOM_PLUGIN = """
from shellbot2.sensorframework.sensor_spec import SensorObservation, SensorSpec

class CustomSensor:
    def __init__(self, label="custom"):
        self.label = label

    async def poll(self, runtime):
        return [
            SensorObservation(
                kind="custom",
                summary=self.label,
                dedupe_key="custom-1",
            )
        ]

SENSOR_SPECS = (
    SensorSpec(
        name="example_sensor",
        description="Custom example sensor",
        factory=lambda runtime: CustomSensor(),
        default_interval_seconds=60,
    ),
)
"""


def test_packaged_discovery_loads_implementations_not_framework():
    specs = discover_sensor_specs()
    assert "disk_usage" in specs
    assert "scheduler" not in specs
    assert "discovery" not in specs
    assert "sensor_spec" not in specs
    assert "config" not in specs


def test_packaged_discovery_loads_valid_module_specs():
    spec, _ = make_spec("packaged_sensor", description="from package")
    module = ModuleType("fake_packaged_sensor")
    module.SENSOR_SPECS = (spec,)

    specs = _discover_from_modules(
        (("fake_packaged_sensor", lambda: module),),
        source="packaged sensors",
    )
    assert specs["packaged_sensor"].description == "from package"


def test_custom_sensor_specs_load(tmp_path):
    sensors_dir = tmp_path / "sensors"
    sensors_dir.mkdir()
    (sensors_dir / "example.py").write_text(CUSTOM_PLUGIN.strip())

    specs = discover_sensor_specs(sensors_dir)
    assert specs["example_sensor"].description == "Custom example sensor"
    assert specs["example_sensor"].default_interval_seconds == 60


def test_custom_spec_overrides_packaged_spec(tmp_path, monkeypatch, caplog):
    packaged, _ = make_spec("example_sensor", description="packaged")
    monkeypatch.setattr(
        "shellbot2.sensorframework.discovery._discover_packaged_specs",
        lambda: {"example_sensor": packaged},
    )
    sensors_dir = tmp_path / "sensors"
    sensors_dir.mkdir()
    (sensors_dir / "example.py").write_text(CUSTOM_PLUGIN.strip())

    caplog.set_level(logging.INFO)
    specs = discover_sensor_specs(sensors_dir)
    assert specs["example_sensor"].description == "Custom example sensor"
    assert "overrides packaged spec" in caplog.text


def test_discovery_skips_failed_modules_and_duplicate_custom_specs(tmp_path, caplog):
    sensors_dir = tmp_path / "sensors"
    sensors_dir.mkdir()
    (sensors_dir / "broken.py").write_text("raise RuntimeError('broken plugin')")
    (sensors_dir / "first.py").write_text(
        """
from shellbot2.sensorframework.sensor_spec import SensorSpec
SENSOR_SPECS = (
    SensorSpec(
        name="duplicate",
        description="first",
        factory=lambda runtime: None,
    ),
)
""".strip()
    )
    (sensors_dir / "second.py").write_text(
        """
from shellbot2.sensorframework.sensor_spec import SensorSpec
SENSOR_SPECS = (
    SensorSpec(
        name="duplicate",
        description="second",
        factory=lambda runtime: None,
    ),
)
""".strip()
    )
    (sensors_dir / "invalid.py").write_text("SENSOR_SPECS = (object(),)")
    (sensors_dir / "empty.py").write_text("VALUE = 1")

    caplog.set_level(logging.ERROR)
    specs = discover_sensor_specs(sensors_dir)

    assert specs["duplicate"].description == "first"
    assert "Failed to import sensor module" in caplog.text
    assert "Ignoring duplicate sensor spec" in caplog.text
    assert "Ignoring invalid sensor spec" in caplog.text
