from __future__ import annotations

import ast
from pathlib import Path

import pytest

from invariant.spec.versioning import InvalidVersion, Version, canon_version_key


def test_canon_version_key_normalizes_release_segments() -> None:
    assert canon_version_key("1") == "1.0.0"
    assert canon_version_key("1.0") == "1.0.0"
    assert canon_version_key("1.0.0") == "1.0.0"
    assert canon_version_key("1.2.3.4") == "1.2.3.4"


def test_canon_version_key_preserves_normalized_version_components() -> None:
    assert canon_version_key("v1.2-alpha_1") == "1.2.0a1"
    assert canon_version_key("1.2preview2") == "1.2.0rc2"
    assert canon_version_key("1.2-3") == "1.2.0.post3"
    assert canon_version_key("1.2dev") == "1.2.0.dev0"
    assert canon_version_key("2!1.2+LOCAL_1") == "2!1.2.0+local.1"


def test_version_release_zeroes_compare_equal() -> None:
    assert Version("1") == Version("1.0") == Version("1.0.0")
    assert hash(Version("1")) == hash(Version("1.0.0"))


def test_version_precedence_matches_invariant_contract() -> None:
    ordered = [
        Version("1.0.dev1"),
        Version("1.0a1.dev1"),
        Version("1.0a1"),
        Version("1.0b1"),
        Version("1.0rc1"),
        Version("1.0"),
        Version("1.0+abc"),
        Version("1.0.post0.dev1"),
        Version("1.0.post0"),
        Version("1.0.post1"),
    ]
    assert ordered == sorted(reversed(ordered))


def test_version_epoch_and_local_ordering() -> None:
    assert Version("1!0") > Version("9999")
    assert Version("1.0+abc.1") < Version("1.0+abc.2")
    assert Version("1.0+abc") < Version("1.0+1")


def test_version_normalizes_aliases_and_separators() -> None:
    assert str(Version("1.0-alpha")) == "1.0a0"
    assert str(Version("1.0_beta_2")) == "1.0b2"
    assert str(Version("1.0pre3")) == "1.0rc3"
    assert str(Version("1.0rev4")) == "1.0.post4"
    assert str(Version("1.0+ABC-1_2")) == "1.0+abc.1.2"


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "x",
        "1..0",
        "1.0+",
        "1.0+abc..x",
        "1.0foo",
        "1.0.postx",
        "1.0.devx",
        "1.0!",
    ],
)
def test_invalid_concrete_versions_are_rejected(raw: str) -> None:
    with pytest.raises(InvalidVersion):
        Version(raw)


def test_runtime_source_has_no_packaging_imports() -> None:
    offenders: list[str] = []

    for path in Path("invariant").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "packaging" or alias.name.startswith("packaging."):
                        offenders.append(f"{path}:{node.lineno}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module == "packaging" or node.module.startswith("packaging."):
                    offenders.append(f"{path}:{node.lineno}")

    assert offenders == []
