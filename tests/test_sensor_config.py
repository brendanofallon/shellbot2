import logging

import pytest

from shellbot2.sensorframework.config import parse_sensors_config, sensors_section_enabled
from tests.sensor_helpers import make_spec


def test_absent_sensors_section_is_disabled(tmp_path):
    assert sensors_section_enabled({}) is False
    assert sensors_section_enabled(None) is False
    config = parse_sensors_config(None, available_specs={})
    assert config.enabled is False
    assert config.entries == ()


def test_disabled_sensors_are_not_resolved(tmp_path):
    spec, _ = make_spec("example_sensor")
    config = parse_sensors_config(
        {
            "enabled": False,
            "entries": [{"name": "example_sensor", "interval_seconds": -1}],
        },
        available_specs={"example_sensor": spec},
    )
    assert config.enabled is False
    assert config.entries == ()


def test_valid_configuration_resolves_entry(tmp_path):
    spec, _ = make_spec("example_sensor", default_interval_seconds=120)
    config = parse_sensors_config(
        {
            "enabled": True,
            "default_interval_seconds": 300,
            "queue_maxsize": 10,
            "entries": [
                {
                    "name": "example_sensor",
                    "enabled": True,
                    "cooldown_seconds": 900,
                    "thread_id": "sensor:example_sensor",
                    "config": {"path": "/"},
                }
            ],
        },
        available_specs={"example_sensor": spec},
    )
    assert config.enabled is True
    assert config.queue_maxsize == 10
    assert len(config.entries) == 1
    entry = config.entries[0]
    assert entry.interval_seconds == 120
    assert entry.cooldown_seconds == 900
    assert entry.thread_id == "sensor:example_sensor"
    assert entry.config == {"path": "/"}


def test_entry_interval_overrides_spec_default(tmp_path):
    spec, _ = make_spec("example_sensor", default_interval_seconds=120)
    config = parse_sensors_config(
        {
            "enabled": True,
            "entries": [{"name": "example_sensor", "interval_seconds": 15}],
        },
        available_specs={"example_sensor": spec},
    )
    assert config.entries[0].interval_seconds == 15


@pytest.mark.parametrize("interval", [0, -5, True, 1.5])
def test_invalid_intervals_fail(tmp_path, interval):
    spec, _ = make_spec("example_sensor")
    with pytest.raises(ValueError, match="interval"):
        parse_sensors_config(
            {
                "enabled": True,
                "entries": [{"name": "example_sensor", "interval_seconds": interval}],
            },
            available_specs={"example_sensor": spec},
        )


def test_duplicate_entries_fail(tmp_path):
    spec, _ = make_spec("example_sensor")
    with pytest.raises(ValueError, match="duplicate"):
        parse_sensors_config(
            {
                "enabled": True,
                "entries": [
                    {"name": "example_sensor"},
                    {"name": "example_sensor"},
                ],
            },
            available_specs={"example_sensor": spec},
        )


def test_missing_plugin_name_fails_when_enabled(tmp_path):
    with pytest.raises(ValueError, match="no plugin"):
        parse_sensors_config(
            {"enabled": True, "entries": [{"name": "missing_sensor"}]},
            available_specs={},
        )


def test_disabled_entry_with_missing_plugin_warns(tmp_path, caplog):
    caplog.set_level(logging.WARNING)
    config = parse_sensors_config(
        {
            "enabled": True,
            "entries": [{"name": "missing_sensor", "enabled": False}],
        },
        available_specs={},
    )
    assert config.enabled is True
    assert config.entries == ()
    assert "unavailable plugin" in caplog.text


def test_default_thread_id(tmp_path):
    spec, _ = make_spec("example_sensor")
    config = parse_sensors_config(
        {"enabled": True, "entries": [{"name": "example_sensor"}]},
        available_specs={"example_sensor": spec},
    )
    assert config.entries[0].thread_id == "sensor:example_sensor"


def test_invalid_thread_id(tmp_path):
    spec, _ = make_spec("example_sensor")
    with pytest.raises(ValueError, match="thread_id"):
        parse_sensors_config(
            {
                "enabled": True,
                "entries": [{"name": "example_sensor", "thread_id": ""}],
            },
            available_specs={"example_sensor": spec},
        )


def test_state_db_configuration_is_rejected(tmp_path):
    spec, _ = make_spec("example_sensor")
    with pytest.raises(ValueError, match="state_db"):
        parse_sensors_config(
            {
                "enabled": True,
                "state_db": str(tmp_path / "elsewhere" / "state.db"),
                "entries": [{"name": "example_sensor"}],
            },
            available_specs={"example_sensor": spec},
        )
