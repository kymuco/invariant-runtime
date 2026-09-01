from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any


_REPO_ROOT = Path(__file__).resolve().parents[2]
_DOCS_ROOT = _REPO_ROOT / "docs"
_LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
_EXTERNAL_PREFIXES = ("http://", "https://", "mailto:")


def _nav_targets(value: Any) -> list[str]:
    targets: list[str] = []
    if isinstance(value, str):
        targets.append(value)
    elif isinstance(value, list):
        for item in value:
            targets.extend(_nav_targets(item))
    elif isinstance(value, dict):
        for item in value.values():
            targets.extend(_nav_targets(item))
    return targets


def test_documentation_site_config_is_reproducible() -> None:
    config = tomllib.loads((_REPO_ROOT / "zensical.toml").read_text(encoding="utf-8"))
    project = config["project"]

    assert project["site_name"] == "Invariant Runtime"
    assert project["docs_dir"] == "docs"
    assert project["site_dir"] == "site"
    assert project["watch"] == ["SECURITY.md", "CAPABILITIES.md"]
    assert project["repo_url"] == "https://github.com/kymuco/invariant-runtime"
    assert project["extra_css"] == ["stylesheets/invariant.css"]

    theme = project["theme"]
    assert theme["variant"] == "modern"
    assert theme["font"] is False
    assert "navigation.instant" in theme["features"]
    assert "search.highlight" in theme["features"]

    extensions = project["markdown_extensions"]
    macros = extensions["zensical"]["extensions"]["macros"]
    assert macros["module_name"] == "docs_macros"
    assert macros["on_error_fail"] is True
    assert macros["on_undefined"] == "strict"
    assert "include_dir" not in macros

    highlight = extensions["pymdownx"]["highlight"]
    assert highlight["line_spans"] == "__span"
    assert highlight["pygments_lang_class"] is True

    superfences = extensions["pymdownx"]["superfences"]
    assert superfences["custom_fences"] == [
        {
            "name": "mermaid",
            "class": "mermaid",
            "format": "pymdownx.superfences.fence_code_format",
        }
    ]

    assert extensions["tables"] == {}

    for target in _nav_targets(project["nav"]):
        if target.startswith(_EXTERNAL_PREFIXES):
            continue
        assert (_DOCS_ROOT / target).is_file(), f"missing nav target: {target}"

    pyproject = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["dependencies"] == []
    assert pyproject["project"]["optional-dependencies"]["docs"] == ["zensical==0.0.57"]


def test_site_markdown_links_stay_inside_docs_root() -> None:
    escaped: list[str] = []

    for page in sorted(_DOCS_ROOT.rglob("*.md")):
        text = page.read_text(encoding="utf-8")
        for raw_target in _LINK_RE.findall(text):
            target = raw_target.strip()
            if not target or target.startswith("#") or target.startswith(_EXTERNAL_PREFIXES):
                continue

            path_part = target.split("#", 1)[0]
            if not path_part:
                continue

            resolved = (page.parent / path_part).resolve()
            if not resolved.is_relative_to(_DOCS_ROOT.resolve()):
                escaped.append(f"{page.relative_to(_REPO_ROOT)} -> {target}")

    assert not escaped, "Documentation site links escape docs/:\n" + "\n".join(escaped)


def test_site_wrappers_keep_root_contracts_canonical() -> None:
    capabilities = (_DOCS_ROOT / "reference" / "capabilities.md").read_text(encoding="utf-8")
    security = (_DOCS_ROOT / "security.md").read_text(encoding="utf-8")
    macros = (_REPO_ROOT / "docs_macros.py").read_text(encoding="utf-8")

    assert capabilities.strip() == '{{ include_contract("CAPABILITIES.md") }}'
    assert security.strip() == '{{ include_contract("SECURITY.md") }}'
    assert 'frozenset({"CAPABILITIES.md", "SECURITY.md"})' in macros
    assert (_REPO_ROOT / "CAPABILITIES.md").is_file()
    assert (_REPO_ROOT / "SECURITY.md").is_file()
