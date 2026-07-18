"""Discover built-in and user-provided tool specifications."""

from collections.abc import Callable, Iterable
import hashlib
import importlib
import importlib.util
import logging
from pathlib import Path
import pkgutil
import sys
from types import ModuleType

from shellbot2.tools.tool_spec import ToolSpec


logger = logging.getLogger(__name__)


def discover_tool_specs(custom_tools_dir: Path | None = None) -> dict[str, ToolSpec]:
    """Return tool specs from the packaged tools directory and user plugins.

    User-provided specs are loaded after packaged specs and may intentionally
    replace a packaged spec with the same configuration name.
    """

    built_in_specs = _discover_packaged_specs()
    custom_specs = _discover_custom_specs(custom_tools_dir)
    built_in_specs.update(custom_specs)
    return built_in_specs


def _discover_packaged_specs() -> dict[str, ToolSpec]:
    package = importlib.import_module("shellbot2.tools")
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
        source="packaged tools",
    )


def _discover_custom_specs(custom_tools_dir: Path | None) -> dict[str, ToolSpec]:
    if custom_tools_dir is None or not custom_tools_dir.is_dir():
        return {}

    module_files = sorted(
        path
        for path in custom_tools_dir.glob("*.py")
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
        source=f"custom tools in {custom_tools_dir}",
    )


def _discover_from_modules(
    modules: Iterable[tuple[str, Callable[[], ModuleType]]],
    *,
    source: str,
) -> dict[str, ToolSpec]:
    specs: dict[str, ToolSpec] = {}
    for module_label, load_module in modules:
        try:
            module = load_module()
        except Exception:
            logger.exception("Failed to import tool module %s from %s", module_label, source)
            continue

        for spec in _get_module_specs(module, module_label, source):
            if spec.name in specs:
                logger.error(
                    "Ignoring duplicate tool spec %r from %s in %s",
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
) -> tuple[ToolSpec, ...]:
    exported_specs = getattr(module, "TOOL_SPECS", ())
    if not exported_specs:
        return ()

    try:
        specs = tuple(exported_specs)
    except TypeError:
        logger.error(
            "Ignoring non-iterable TOOL_SPECS exported by %s from %s",
            module_label,
            source,
        )
        return ()

    valid_specs: list[ToolSpec] = []
    for spec in specs:
        if not isinstance(spec, ToolSpec):
            logger.error(
                "Ignoring invalid tool spec from %s in %s: expected ToolSpec, got %s",
                module_label,
                source,
                type(spec).__name__,
            )
            continue
        valid_specs.append(spec)
    return tuple(valid_specs)


def _load_custom_module(module_file: Path) -> ModuleType:
    source_hash = hashlib.sha256(str(module_file.resolve()).encode()).hexdigest()[:16]
    module_name = f"shellbot2_custom_tool_{module_file.stem}_{source_hash}"
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
