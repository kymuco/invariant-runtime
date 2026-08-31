from __future__ import annotations

import ast
import sys
from pathlib import Path


def test_runtime_has_no_third_party_imports() -> None:
    runtime_root = Path(__file__).resolve().parents[2] / "invariant"
    third_party: list[str] = []

    for path in sorted(runtime_root.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))

        for node in ast.walk(tree):
            names: list[str] = []

            if isinstance(node, ast.Import):
                names.extend(alias.name.split(".", 1)[0] for alias in node.names)

            elif isinstance(node, ast.ImportFrom):
                if node.level > 0 or not node.module:
                    continue
                names.append(node.module.split(".", 1)[0])

            for name in names:
                if name == "invariant" or name in sys.stdlib_module_names:
                    continue
                third_party.append(f"{path.relative_to(runtime_root.parent)}:{node.lineno}: {name}")

    assert third_party == [], "third-party runtime imports found:\n" + "\n".join(third_party)
