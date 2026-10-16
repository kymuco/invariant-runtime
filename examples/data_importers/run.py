"""Import order data through versioned plugins; no framework internals."""

from __future__ import annotations

import argparse
from decimal import Decimal
from pathlib import Path

from invariant.api import PluginService


EXAMPLE_DIR = Path(__file__).resolve().parent


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Summarize order files using Invariant plugins."
    )
    parser.add_argument(
        "files", nargs="*", type=Path, help="Order CSV/JSON files (defaults to samples)"
    )
    parser.add_argument(
        "--plugin-dir", type=Path, action="append", default=[],
        help="Additional plugin manifest directory; repeatable"
    )
    args = parser.parse_args()
    files = args.files or [
        EXAMPLE_DIR / "samples" / "orders.csv",
        EXAMPLE_DIR / "samples" / "orders.json",
    ]

    service = PluginService.from_manifest_paths(
        [EXAMPLE_DIR / "plugins", *args.plugin_dir]
    )
    try:
        for path in files:
            plugin_id = f"example:orders-{path.suffix.removeprefix('.').lower()}"
            if not service.has_plugin(plugin_id):
                parser.error(f"No importer for {path.suffix or '(no extension)'}")
            rows = service.get(plugin_id).load(path)
            total = sum(
                (
                    Decimal(str(row["quantity"])) * Decimal(str(row["unit_price"]))
                    for row in rows
                ),
                Decimal("0"),
            )
            noun = "item" if len(rows) == 1 else "items"
            print(
                f"{path.name}: {len(rows)} {noun}, total ${total:.2f} via {plugin_id}"
            )
    finally:
        service.close()


if __name__ == "__main__":
    main()
