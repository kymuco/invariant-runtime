from __future__ import annotations

from importlib.metadata import version as distribution_version
from importlib.resources import files

import invariant
import invariant.api as api
import invariant.spec as spec


_ROOT_PUBLIC = {"__version__"}
_API_PUBLIC = {
    "AmbiguousPluginReference",
    "BasePolicy",
    "CapabilityError",
    "CapabilityNotFound",
    "CapabilityUnavailableError",
    "CapabilityVersionMismatch",
    "DependencyCycleError",
    "EphemeralPolicy",
    "ManagedPolicy",
    "PluginError",
    "PluginLoadError",
    "PluginNotFoundError",
    "PluginService",
    "PolicyProfile",
    "SandboxLimits",
    "WorkspaceOptions",
    "ephemeral_policy_for_profile",
    "normalize_profile",
    "profile_from_env",
    "sandbox_limits_for_profile",
}
_SPEC_PUBLIC = {
    "BasePlugin",
    "DependencyScope",
    "DependencySpec",
    "InvalidVersion",
    "PluginContext",
    "PluginMeta",
    "Version",
    "canon_version_key",
}


def _assert_explicit_exports(module, expected: set[str]) -> None:  # noqa: ANN001
    assert set(module.__all__) == expected
    for name in expected:
        assert hasattr(module, name), name


def test_root_public_contract_exposes_distribution_version_only() -> None:
    _assert_explicit_exports(invariant, _ROOT_PUBLIC)
    assert invariant.__version__ == distribution_version("invariant-runtime")


def test_host_api_exports_are_explicit() -> None:
    _assert_explicit_exports(api, _API_PUBLIC)


def test_plugin_author_spec_exports_are_explicit() -> None:
    _assert_explicit_exports(spec, _SPEC_PUBLIC)


def test_internal_descriptor_is_not_promoted_to_public_spec() -> None:
    assert "PluginDescriptor" not in spec.__all__
    assert not hasattr(spec, "PluginDescriptor")


def test_pep561_marker_is_packaged() -> None:
    assert files("invariant").joinpath("py.typed").is_file()
