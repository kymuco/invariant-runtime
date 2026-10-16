"""JSON order reader implementing the same host-defined interface."""

import json
from pathlib import Path

from invariant.spec import BasePlugin


class JsonOrdersPlugin(BasePlugin):
    def initialize(self, **deps) -> None:
        pass

    def shutdown(self) -> None:
        pass

    def load(self, path: str | Path) -> list[dict[str, str]]:
        rows = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(rows, list):
            raise ValueError("Expected a JSON array of orders")
        return rows
