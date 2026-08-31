from __future__ import annotations

import re
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[2]
_MARKDOWN_LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
_EXTERNAL_PREFIXES = ("http://", "https://", "mailto:")


def _markdown_files() -> list[Path]:
    files = [
        _REPO_ROOT / "README.md",
        _REPO_ROOT / "CAPABILITIES.md",
        _REPO_ROOT / "SECURITY.md",
    ]
    files.extend(sorted((_REPO_ROOT / "docs").rglob("*.md")))
    files.extend(sorted((_REPO_ROOT / "examples").rglob("*.md")))
    return files


def test_documentation_relative_links_resolve() -> None:
    broken: list[str] = []

    for page in _markdown_files():
        text = page.read_text(encoding="utf-8")
        for raw_target in _MARKDOWN_LINK_RE.findall(text):
            target = raw_target.strip()
            if not target or target.startswith("#") or target.startswith(_EXTERNAL_PREFIXES):
                continue

            path_part = target.split("#", 1)[0]
            if not path_part:
                continue

            resolved = (page.parent / path_part).resolve()
            if not resolved.exists():
                broken.append(
                    f"{page.relative_to(_REPO_ROOT)} -> {target} "
                    f"(resolved {resolved})"
                )

    assert not broken, "Broken documentation links:\n" + "\n".join(broken)


def test_checked_example_keeps_public_import_boundaries() -> None:
    plugin_source = (_REPO_ROOT / "examples" / "hello_plugin" / "plugin.py").read_text(
        encoding="utf-8"
    )
    host_source = (_REPO_ROOT / "examples" / "hello_plugin" / "run.py").read_text(
        encoding="utf-8"
    )

    assert "from invariant.spec import" in plugin_source
    assert "invariant.load" not in plugin_source
    assert "invariant.catalog" not in plugin_source
    assert "invariant.exec" not in plugin_source
    assert "invariant.ops" not in plugin_source

    assert "from invariant.api import PluginService" in host_source
    assert "invariant.load" not in host_source
    assert "invariant.catalog" not in host_source
    assert "invariant.exec" not in host_source
    assert "invariant.ops" not in host_source
