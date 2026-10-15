from __future__ import annotations

import re
import tomllib
import xml.etree.ElementTree as ET
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

    features = set(theme["features"])
    assert {
        "navigation.instant",
        "navigation.tracking",
        "navigation.sections",
        "navigation.path",
        "navigation.top",
        "navigation.footer",
        "toc.follow",
        "search.highlight",
        "content.code.copy",
        "content.tooltips",
    } <= features

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


def test_documentation_home_is_a_product_front_door() -> None:
    home = (_DOCS_ROOT / "index.md").read_text(encoding="utf-8")
    styles = (_DOCS_ROOT / "stylesheets" / "invariant.css").read_text(encoding="utf-8")

    assert "Plugins have lifetimes. Give them an owner." in home
    assert "iv-opening" in home
    assert "iv-editorial" in home
    assert "iv-ledger" in home
    assert "iv-note" in home
    assert "Make a plugin" in home
    assert "hide:\n  - toc" in home
    assert "No mandatory third-party runtime dependencies." not in home
    assert "standard library" in home
    assert "not a hostile-code sandbox" in home
    assert "iv-path-grid" not in home
    assert "iv-stage-track" not in home

    for selector in (
        ".iv-opening",
        ".iv-figure",
        ".iv-editorial",
        ".iv-ledger",
        ".iv-note",
        ".iv-route-list",
    ):
        assert selector in styles

    # The selected design intentionally avoids Runplane-like step/card strips.
    assert ".iv-stage-track" not in styles
    assert ".iv-path-grid" not in styles

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


def test_invariant_hero_is_local_accessible_and_theme_aware() -> None:
    home = (_DOCS_ROOT / "index.md").read_text(encoding="utf-8")
    readme = (_REPO_ROOT / "README.md").read_text(encoding="utf-8")

    for theme in ("light", "dark"):
        asset = _DOCS_ROOT / "assets" / f"invariant-hero-{theme}.svg"
        content = asset.read_text(encoding="utf-8")
        svg = ET.fromstring(content)
        ns = "{http://www.w3.org/2000/svg}"
        assert svg.tag == ns + "svg"
        assert svg.attrib["viewBox"] == "0 0 1440 480"
        assert svg.attrib["role"] == "img"
        assert svg.find(ns + "title") is not None
        assert svg.find(ns + "desc") is not None
        assert "fixed-point" in content
        assert "MANIFEST" in content
        assert "PLUGIN" in content
        assert "CAPABILITY" in content
        assert "Small core. Clear ownership." in content
        assert "LIFECYCLE / 04 STATES" not in content
        assert "<script" not in content
        assert "<image" not in content
        assert "http://" not in content.replace("http://www.w3.org/2000/svg", "")
        assert "https://" not in content
        assert f"assets/invariant-hero-{theme}.svg" in home
        assert f"docs/assets/invariant-hero-{theme}.svg" in readme

    assert "CONTRIBUTING.md" in readme
    assert "SECURITY.md" in readme
    for name in ("CONTRIBUTING.md", "SUPPORT.md"):
        assert (_REPO_ROOT / name).is_file()


def test_invariant_identity_is_independent_and_dependent_free() -> None:
    readme = (_REPO_ROOT / "README.md").read_text(encoding="utf-8")
    home = (_DOCS_ROOT / "index.md").read_text(encoding="utf-8")
    styles = (_DOCS_ROOT / "stylesheets" / "invariant.css").read_text(
        encoding="utf-8"
    )
    style_guide = (_DOCS_ROOT / "STYLE.md").read_text(encoding="utf-8")

    for name in ("runplane", "character_os", "capability_lab"):
        assert name not in readme.casefold()
        assert name not in home.casefold()

    assert "zero mandatory third-party runtime dependencies" in readme.lower()
    assert "invariant.api" in readme
    assert "invariant.spec" in readme
    assert "invariant.api" in home
    assert "invariant.spec" in home

    for token in ("#5b3db0", "#267f73", "#fbfafe", "#151126"):
        assert token in styles.lower()
    for label in ("Iris / mineral", "Cobalt / ember", "Pine / citrus"):
        assert label in style_guide
