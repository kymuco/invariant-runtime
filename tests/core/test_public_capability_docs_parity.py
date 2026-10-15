"""Published built-in capability versions must track the public code contract.

A manifest can pin a concrete capability version, so a stale docs table
silently tells plugin authors to request an unsupported version.
"""
from __future__ import annotations

import re
from pathlib import Path

import invariant.spec as spec


ROOT = Path(__file__).resolve().parents[2]
NAMES = (
    "FS_SANDBOX_CAPABILITY",
    "WORKSPACE_CAPABILITY",
    "HTTP_CAPABILITY",
    "KEY_VALUE_CAPABILITY",
    "PROCESS_CAPABILITY",
    "CONFIG_SOURCE_CAPABILITY",
    "INSPECTOR_CAPABILITY",
)
_ROW = re.compile(
    r"^\| `(?P<name>[A-Z_]+_CAPABILITY)` \| "
    r"`(?P<ref>(?:invariant:capability\\.)?[a-z_]+@[0-9.]+)`",
    re.MULTILINE,
)


def _documented_refs(path: Path) -> dict[str, str]:
    content = path.read_text(encoding="utf-8")
    found: dict[str, str] = {}
    for match in _ROW.finditer(content):
        name = match.group("name")
        assert name not in found, f"Duplicate capability row in {path}: {name}"
        suffix = match.group("ref").removeprefix("invariant:capability.")
        found[name] = f"invariant:capability.{suffix}"
    return found


def test_public_capability_version_tables_match_python_contract() -> None:
    expected = {name: getattr(spec, name).ref for name in NAMES}
    docs = (
        ROOT / "docs" / "reference" / "public-api.md",
        ROOT / "CAPABILITIES.md",
    )
    for page in docs:
        actual = _documented_refs(page)
        assert actual == expected, (
            f"Stale built-in capability versions in {page.relative_to(ROOT)}: "
            f"documented={actual!r}, public={expected!r}"
        )
