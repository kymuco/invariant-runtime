"""Current manifest path-security behavior before any performance change.

This is an observational regression suite. It neither caches resolved paths
nor weakens plugin-root containment checks.
"""

from __future__ import annotations

import errno
import json
import subprocess
import sys
from pathlib import Path

import pytest

from invariant.api import PluginService
from invariant.load.sources.manifest import ManifestSource
from invariant.spec.errors import PluginLoadError


def _plugin(
    root: Path,
    *,
    plugin_id: str = "example:secure",
    version: str = "1.0.0",
    code_rel: str = "plugin.py",
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "manifest_version": 1,
                "id": plugin_id,
                "version": version,
                "name": "Path security fixture",
                "source": {
                    "code_path": code_rel,
                    "class_name": "ExamplePlugin",
                },
            }
        ),
        encoding="utf-8",
    )
    return root / "manifest.json"


def _code(target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "from invariant.spec import BasePlugin\n"
        "class ExamplePlugin(BasePlugin):\n"
        "    pass\n",
        encoding="utf-8",
    )
    return target


def _read(manifest: Path):
    return ManifestSource([manifest.parent])._read_manifest(manifest, dev_mode=False)


def _symlink(link: Path, target: Path, *, is_directory: bool = False) -> None:
    try:
        link.symlink_to(target, target_is_directory=is_directory)
    except NotImplementedError as exc:
        pytest.skip(f"Symlinks unsupported by this Python host: {exc}")
    except OSError as exc:
        permissions = {errno.EACCES, errno.EPERM, errno.ENOTSUP}
        if exc.errno in permissions or getattr(exc, "winerror", None) in {
            5,
            50,
            1314,
        }:
            pytest.skip(f"OS policy forbids symlink creation: {exc}")
        raise


def test_manifest_path_contract_accepts_nested_code_and_preserves_descriptor(
    tmp_path: Path,
) -> None:
    root = tmp_path / "nested"
    manifest = _plugin(root, code_rel="src/plugin.py")
    target = _code(root / "src" / "plugin.py")

    descriptor = _read(manifest)

    assert descriptor.meta.id == "example:secure"
    assert descriptor.meta.version is not None
    assert descriptor.source.path == target.resolve()
    assert descriptor.source.extra["plugin_root"] == str(root.resolve())
    assert descriptor.source.extra["manifest_path"] == str(manifest)
    assert descriptor.source.extra["code_rel"] == "src/plugin.py"
    assert descriptor.source.module.startswith("invariant_manifest_example_secure_")


@pytest.mark.parametrize("code_rel", ["../outside.py", "../../outside.py"])
def test_manifest_path_contract_rejects_relative_parent_escape(
    tmp_path: Path, code_rel: str
) -> None:
    root = tmp_path / "container" / "plugin"
    manifest = _plugin(root, code_rel=code_rel)
    _code((root / code_rel).resolve())

    with pytest.raises(PluginLoadError, match="must be under plugin_root"):
        _read(manifest)


def test_manifest_path_contract_rejects_absolute_external_code(
    tmp_path: Path,
) -> None:
    external = _code(tmp_path / "outside" / "plugin.py")
    manifest = _plugin(tmp_path / "plugin", code_rel=str(external))

    with pytest.raises(PluginLoadError, match="must be under plugin_root"):
        _read(manifest)


def test_manifest_path_contract_rejects_external_symlink_target(
    tmp_path: Path,
) -> None:
    external = _code(tmp_path / "elsewhere" / "plugin.py")
    root = tmp_path / "plugin"
    manifest = _plugin(root, code_rel="link.py")
    _symlink(root / "link.py", external)

    with pytest.raises(PluginLoadError, match="must be under plugin_root"):
        _read(manifest)


def test_manifest_path_contract_allows_symlink_into_same_plugin_root(
    tmp_path: Path,
) -> None:
    root = tmp_path / "plugin"
    manifest = _plugin(root, code_rel="link.py")
    target = _code(root / "src" / "plugin.py")
    _symlink(root / "link.py", target)

    descriptor = _read(manifest)

    assert descriptor.source.path == target.resolve()


def test_manifest_path_contract_revalidates_symlink_after_retarget(
    tmp_path: Path,
) -> None:
    root = tmp_path / "plugin"
    manifest = _plugin(root, code_rel="link.py")
    internal = _code(root / "src" / "plugin.py")
    external = _code(tmp_path / "outside" / "plugin.py")
    link = root / "link.py"
    _symlink(link, internal)

    first = _read(manifest)
    assert first.source.path == internal.resolve()

    link.unlink()
    _symlink(link, external)
    with pytest.raises(PluginLoadError, match="must be under plugin_root"):
        _read(manifest)


def _junction(link: Path, target: Path) -> None:
    """Create an NTFS directory junction without symlink privileges."""
    created = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        cwd=link.parent,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if created.returncode != 0:
        pytest.skip("Windows junction creation unavailable in this environment")


@pytest.mark.skipif(sys.platform != "win32", reason="NTFS junction-specific")
def test_manifest_path_contract_rejects_external_windows_junction(
    tmp_path: Path,
) -> None:
    external_dir = tmp_path / "outside"
    _code(external_dir / "plugin.py")
    root = tmp_path / "plugin"
    manifest = _plugin(root, code_rel="linked/plugin.py")
    junction = root / "linked"
    _junction(junction, external_dir)

    try:
        with pytest.raises(PluginLoadError, match="must be under plugin_root"):
            _read(manifest)
    finally:
        junction.rmdir()
    assert (external_dir / "plugin.py").is_file()


@pytest.mark.skipif(sys.platform != "win32", reason="NTFS junction-specific")
def test_manifest_path_contract_allows_internal_windows_junction(
    tmp_path: Path,
) -> None:
    root = tmp_path / "plugin"
    manifest = _plugin(root, code_rel="linked/plugin.py")
    internal = _code(root / "src" / "plugin.py")
    junction = root / "linked"
    _junction(junction, internal.parent)

    try:
        descriptor = _read(manifest)
        assert descriptor.source.path == internal.resolve()
    finally:
        junction.rmdir()
    assert internal.is_file()


@pytest.mark.skipif(sys.platform != "win32", reason="NTFS junction-specific")
def test_manifest_path_contract_revalidates_junction_between_loads(
    tmp_path: Path,
) -> None:
    root = tmp_path / "plugin"
    manifest = _plugin(root, code_rel="linked/plugin.py")
    internal = _code(root / "src" / "plugin.py")
    external = _code(tmp_path / "outside" / "plugin.py")
    junction = root / "linked"
    _junction(junction, internal.parent)

    try:
        first = _read(manifest)
        assert first.source.path == internal.resolve()

        # Remove only the junction entry, not the target directory.
        junction.rmdir()
        assert internal.is_file()
        _junction(junction, external.parent)

        with pytest.raises(PluginLoadError, match="must be under plugin_root"):
            _read(manifest)
    finally:
        if junction.exists():
            junction.rmdir()
    assert external.is_file()
    assert internal.is_file()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows casing semantics")
def test_manifest_path_contract_windows_case_insensitive_code_resolution(
    tmp_path: Path,
) -> None:
    root = tmp_path / "plugin"
    manifest = _plugin(root, code_rel="PLUGIN.PY")
    target = _code(root / "plugin.py")

    descriptor = _read(manifest)

    assert descriptor.source.path.samefile(target)


def test_manifest_path_contract_rejects_missing_code(
    tmp_path: Path,
) -> None:
    manifest = _plugin(tmp_path / "plugin")
    with pytest.raises(PluginLoadError, match="Plugin code file not found"):
        _read(manifest)


def test_manifest_path_contract_rejects_oversized_manifest(
    tmp_path: Path,
) -> None:
    manifest = _plugin(tmp_path / "plugin")
    _code(manifest.parent / "plugin.py")
    manifest.write_text(" " * 1_000_001, encoding="utf-8")

    with pytest.raises(PluginLoadError, match="Manifest too large"):
        _read(manifest)


def test_manifest_path_contract_rejects_malformed_json(
    tmp_path: Path,
) -> None:
    manifest = _plugin(tmp_path / "plugin")
    _code(manifest.parent / "plugin.py")
    manifest.write_text("{ not valid json", encoding="utf-8")

    with pytest.raises(PluginLoadError, match="Failed to read manifest"):
        _read(manifest)


def test_manifest_discovery_observes_changed_content_between_creates(
    tmp_path: Path,
) -> None:
    root = tmp_path / "plugin"
    manifest = _plugin(root, version="1.0.0")
    _code(root / "plugin.py")

    first = _read(manifest)
    _plugin(root, version="2.0.0")
    second = _read(manifest)

    assert str(first.meta.version) != str(second.meta.version)
    assert first.source.module != second.source.module or (
        first.meta.version != second.meta.version
    )


def test_manifest_service_inventory_order_is_stable_across_search_order(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    third = tmp_path / "third"
    _plugin(first, plugin_id="example:alpha", version="1.2.0")
    _plugin(second, plugin_id="example:alpha", version="1.10.0")
    _plugin(third, plugin_id="example:beta", version="2.0.0")
    for root in (first, second, third):
        _code(root / "plugin.py")

    snapshots = []
    for paths in ([first, second, third], [third, second, first]):
        service = PluginService.from_manifest_paths(paths)
        try:
            snapshots.append(
                [(item.id, item.version) for item in service.list_plugins()]
            )
        finally:
            service.close()

    assert (
        snapshots[0]
        == snapshots[1]
        == [
            ("example:alpha", "1.2.0"),
            ("example:alpha", "1.10.0"),
            ("example:beta", "2.0.0"),
        ]
    )


def test_manifest_path_contract_no_cross_create_cache(
    tmp_path: Path,
) -> None:
    root = tmp_path / "plugin"
    manifest = _plugin(root)
    _code(root / "plugin.py")

    source = ManifestSource([root])
    before = source._read_manifest(manifest, dev_mode=False)
    (root / "plugin.py").unlink()
    with pytest.raises(PluginLoadError, match="Plugin code file not found"):
        source._read_manifest(manifest, dev_mode=False)

    assert before.source.path == (root / "plugin.py").resolve()
