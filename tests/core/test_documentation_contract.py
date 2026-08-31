from __future__ import annotations

import json
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


def _fenced_block_after(text: str, marker: str) -> str:
    marker_pos = text.index(marker)
    fence_pos = text.index("```", marker_pos)
    content_start = text.index("\n", fence_pos) + 1
    content_end = text.index("\n```", content_start)
    return text[content_start:content_end].strip()


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


def test_getting_started_snippets_match_checked_example() -> None:
    page = (_REPO_ROOT / "docs" / "getting-started.md").read_text(encoding="utf-8")
    example_root = _REPO_ROOT / "examples" / "hello_plugin"

    expected = {
        "Create `hello_plugin/plugin.py`:": example_root / "plugin.py",
        "Create `hello_plugin/manifest.json`:": example_root / "manifest.json",
        "Create `hello_plugin/run.py`:": example_root / "run.py",
    }

    for marker, path in expected.items():
        assert _fenced_block_after(page, marker) == path.read_text(encoding="utf-8").strip()

    manifest = json.loads((example_root / "manifest.json").read_text(encoding="utf-8"))
    assert set(manifest) == {"manifest_version", "id", "version", "source"}
