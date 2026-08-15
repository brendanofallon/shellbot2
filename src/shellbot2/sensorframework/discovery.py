"""Discover built-in and user-provided sensor specifications."""

from collections.abc import Callable, Iterable
import hashlib
import importlib
import importlib.util
import logging
from pathlib import Path
import pkgutil
import sys
from types import ModuleType

from shellbot2.sensorframework.sensor_spec import SensorSpec


logger = logging.getLogger(__name__)

PACKAGED_SENSORS_PACKAGE = "shellbot2.sensors"


def discover_sensor_specs(custom_sensors_dir: Path | None = None) -> dict[str, SensorSpec]:
    """Return sensor specs from the packaged sensors package and user plugins.

    Packaged implementations are loaded from ``shellbot2.sensors``, not from
    this framework package. Custom specs are loaded from ``<datadir>/sensors``.
    Packaged specs are gathered first. A valid custom spec with the same name
    replaces the packaged spec and the override is logged. Duplicate names
    within the same source are rejected. Import errors, invalid exports, and
    invalid spec objects are logged and skipped so one broken plugin cannot
    stop discovery.
    """

    specs = _discover_packaged_specs()
    custom_specs = _discover_custom_specs(custom_sensors_dir)
    for name, spec in custom_specs.items():
        if name in specs:
            logger.info(
                "Custom sensor spec %r from %s overrides packaged spec",
                name,
                custom_sensors_dir,
            )
        specs[name] = spec
    return specs


def _discover_packaged_specs() -> dict[str, SensorSpec]:
    package = importlib.import_module(PACKAGED_SENSORS_PACKAGE)
    module_names = sorted(
        module_info.name
        for module_info in pkgutil.iter_modules(
            package.__path__,
            prefix=f"{package.__name__}.",
        )
        if not module_info.name.rsplit(".", maxsplit=1)[-1].startswith("_")
    )
    return _discover_from_modules(
        (
            (module_name, lambda module_name=module_name: importlib.import_module(module_name))
            for module_name in module_names
        ),
        source="packaged sensors",
    )


def _discover_custom_specs(custom_sensors_dir: Path | None) -> dict[str, SensorSpec]:
    if custom_sensors_dir is None or not custom_sensors_dir.is_dir():
        return {}

    module_files = sorted(
        path
        for path in custom_sensors_dir.glob("*.py")
        if not path.name.startswith("_")
    )
    return _discover_from_modules(
        (
            (
                str(module_file),
                lambda module_file=module_file: _load_custom_module(module_file),
            )
            for module_file in module_files
        ),
        source=f"custom sensors in {custom_sensors_dir}",
    )


def _discover_from_modules(
    modules: Iterable[tuple[str, Callable[[], ModuleType]]],
    *,
    source: str,
) -> dict[str, SensorSpec]:
    specs: dict[str, SensorSpec] = {}
    for module_label, load_module in modules:
        try:
            module = load_module()
        except Exception:
            logger.exception("Failed to import sensor module %s from %s", module_label, source)
            continue

        for spec in _get_module_specs(module, module_label, source):
            if spec.name in specs:
                logger.error(
                    "Ignoring duplicate sensor spec %r from %s in %s",
                    spec.name,
                    module_label,
                    source,
                )
                continue
            specs[spec.name] = spec
    return specs


def _get_module_specs(
    module: ModuleType,
    module_label: str,
    source: str,
) -> tuple[SensorSpec, ...]:
    exported_specs = getattr(module, "SENSOR_SPECS", ())
    if not exported_specs:
        return ()

    try:
        specs = tuple(exported_specs)
    except TypeError:
        logger.error(
            "Ignoring non-iterable SENSOR_SPECS exported by %s from %s",
            module_label,
            source,
        )
        return ()

    valid_specs: list[SensorSpec] = []
    for spec in specs:
        if not isinstance(spec, SensorSpec):
            logger.error(
                "Ignoring invalid sensor spec from %s in %s: expected SensorSpec, got %s",
                module_label,
                source,
                type(spec).__name__,
            )
            continue
        valid_specs.append(spec)
    return tuple(valid_specs)


def _load_custom_module(module_file: Path) -> ModuleType:
    source_hash = hashlib.sha256(str(module_file.resolve()).encode()).hexdigest()[:16]
    module_name = f"shellbot2_custom_sensor_{module_file.stem}_{source_hash}"
    module_spec = importlib.util.spec_from_file_location(module_name, module_file)
    if module_spec is None or module_spec.loader is None:
        raise ImportError(f"Could not create an import specification for {module_file}")

    module = importlib.util.module_from_spec(module_spec)
    sys.modules[module_name] = module
    try:
        module_spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module
