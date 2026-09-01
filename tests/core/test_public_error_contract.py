from __future__ import annotations

from pathlib import Path

import pytest

from invariant.api import (
    AmbiguousPluginReference,
    CapabilityError,
    CapabilityNotFound,
    CapabilityUnavailableError,
    CapabilityVersionMismatch,
    DependencyCycleError,
    PluginError,
    PluginLoadError,
    PluginNotFoundError,
    PluginService,
)
from invariant.api import errors as public_errors
from invariant.exec.caps.registry import (
    CapabilityError as InternalCapabilityError,
    CapabilityNotFound as InternalCapabilityNotFound,
    CapabilityUnavailableError as InternalCapabilityUnavailableError,
    CapabilityVersionMismatch as InternalCapabilityVersionMismatch,
)
from invariant.spec.errors import (
    AmbiguousPluginReference as InternalAmbiguousPluginReference,
    DependencyCycleError as InternalDependencyCycleError,
    PluginError as InternalPluginError,
    PluginLoadError as InternalPluginLoadError,
    PluginNotFoundError as InternalPluginNotFoundError,
)


_REPO_ROOT = Path(__file__).resolve().parents[2]
_HELLO_EXAMPLE_DIR = _REPO_ROOT / "examples" / "hello_plugin"

_PUBLIC_ERROR_NAMES = {
    "AmbiguousPluginReference",
    "CapabilityError",
    "CapabilityNotFound",
    "CapabilityUnavailableError",
    "CapabilityVersionMismatch",
    "DependencyCycleError",
    "PluginError",
    "PluginLoadError",
    "PluginNotFoundError",
}


def test_public_error_module_exports_are_explicit() -> None:
    assert set(public_errors.__all__) == _PUBLIC_ERROR_NAMES
    for name in _PUBLIC_ERROR_NAMES:
        assert hasattr(public_errors, name), name


def test_public_errors_preserve_runtime_class_identity() -> None:
    assert PluginError is InternalPluginError
    assert PluginLoadError is InternalPluginLoadError
    assert PluginNotFoundError is InternalPluginNotFoundError
    assert DependencyCycleError is InternalDependencyCycleError
    assert AmbiguousPluginReference is InternalAmbiguousPluginReference

    assert CapabilityError is InternalCapabilityError
    assert CapabilityNotFound is InternalCapabilityNotFound
    assert CapabilityUnavailableError is InternalCapabilityUnavailableError
    assert CapabilityVersionMismatch is InternalCapabilityVersionMismatch


def test_public_error_hierarchies_match_runtime_behavior() -> None:
    assert issubclass(PluginNotFoundError, PluginError)
    assert issubclass(PluginLoadError, PluginError)
    assert issubclass(DependencyCycleError, PluginError)
    assert issubclass(AmbiguousPluginReference, PluginError)

    assert issubclass(CapabilityNotFound, CapabilityError)
    assert issubclass(CapabilityVersionMismatch, CapabilityError)
    assert issubclass(CapabilityUnavailableError, RuntimeError)


def test_missing_plugin_is_catchable_from_public_api() -> None:
    service = PluginService.from_manifest_paths([_HELLO_EXAMPLE_DIR])
    try:
        with pytest.raises(PluginNotFoundError, match="Plugin not found"):
            service.get("example:missing")
    finally:
        service.close()
