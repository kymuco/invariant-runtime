"""Temporal path-resolution regression guards for the current manifest loader.

The fixture simulates path canonicalization changing between the first
resolution and containment recheck in a single _read_manifest invocation.
It requires no filesystem symlink privileges and does not purport to prove
OS-level race safety.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from invariant.load.sources.manifest import ManifestSource
from invariant.spec.errors import PluginLoadError


def _write_manifest(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "plugin.py").write_text("class ExamplePlugin: pass\n", encoding="utf-8")
    manifest = root / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "manifest_version": 1,
                "id": "example:time-of-check",
                "version": "1.0.0",
                "source": {
                    "code_path": "plugin.py",
                    "class_name": "ExamplePlugin",
                },
            }
        ),
        encoding="utf-8",
    )
    return manifest


@pytest.mark.parametrize("retarget", ["source_path", "plugin_root"])
def test_manifest_containment_rechecks_canonical_paths_within_one_load(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    retarget: str,
) -> None:
    root = tmp_path / "plugin"
    manifest = _write_manifest(root)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "plugin.py").write_text("pass\n", encoding="utf-8")

    real_resolve = Path.resolve
    resolved_root = real_resolve(root)
    resolved_code = real_resolve(root / "plugin.py")
    resolved_outside = real_resolve(outside)

    watched = resolved_code if retarget == "source_path" else resolved_root
    new_target = (
        resolved_outside / "plugin.py"
        if retarget == "source_path"
        else resolved_outside
    )
    calls = 0

    def simulated_retarget(path: Path, *args, **kwargs) -> Path:
        nonlocal calls
        if path == watched:
            calls += 1
            if calls == 2:
                return new_target
        return real_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", simulated_retarget)

    # The original implementation resolves root/source once to build
    # canonical paths, then resolves each again to validate containment.
    # A candidate that removes either second resolution fails this test.
    with pytest.raises(PluginLoadError, match="must be under plugin_root"):
        ManifestSource([root])._read_manifest(manifest, dev_mode=False)

    assert calls == 2


def test_manifest_source_still_accepts_unchanged_canonical_paths(
    tmp_path: Path,
) -> None:
    root = tmp_path / "plugin"
    manifest = _write_manifest(root)

    descriptor = ManifestSource([root])._read_manifest(manifest, dev_mode=False)

    assert descriptor.source.path == (root / "plugin.py").resolve()
    assert descriptor.source.extra["plugin_root"] == str(root.resolve())
