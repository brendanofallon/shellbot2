"""Sensor plugin framework for ShellBot2.

This package owns scheduling, discovery, configuration, state, and prompt
rendering. Concrete sensor implementations live in ``shellbot2.sensors``.

Sensors emit structured observations (data, never instructions). Only
framework-owned code turns those observations into an agent prompt.
"""

from shellbot2.sensorframework.discovery import discover_sensor_specs
from shellbot2.sensorframework.sensor_spec import (
    JSONValue,
    Sensor,
    SensorFactory,
    SensorObservation,
    SensorRuntime,
    SensorSpec,
    SensorStateStore,
)

__all__ = [
    "JSONValue",
    "Sensor",
    "SensorFactory",
    "SensorObservation",
    "SensorRuntime",
    "SensorSpec",
    "SensorStateStore",
    "discover_sensor_specs",
]
