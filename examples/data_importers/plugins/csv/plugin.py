"""CSV order reader implementing the application's load(path) contract."""

import csv
from pathlib import Path

from invariant.spec import BasePlugin


class CsvOrdersPlugin(BasePlugin):
    def initialize(self, **deps) -> None:
        pass

    def shutdown(self) -> None:
        pass

    def load(self, path: str | Path) -> list[dict[str, str]]:
        with Path(path).open(newline="", encoding="utf-8") as stream:
            return list(csv.DictReader(stream))
