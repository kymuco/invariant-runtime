"""First-run proof, including external format extensibility."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "examples" / "data_importers" / "run.py"


def _run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(RUNNER), *args],
        cwd=cwd, check=True, capture_output=True, text=True,
    )


def test_default_importers_work_outside_checkout(tmp_path: Path) -> None:
    result = _run(cwd=tmp_path)
    assert result.stdout.splitlines() == [
        "orders.csv: 2 items, total $30.25 via example:orders-csv",
        "orders.json: 2 items, total $35.50 via example:orders-json",
    ]


def test_optional_tsv_addon_is_not_loaded_until_requested(tmp_path: Path) -> None:
    sample = ROOT / "examples" / "data_importers" / "samples" / "orders.tsv"
    plugins = ROOT / "examples" / "data_importers" / "addons"
    result = _run("--plugin-dir", str(plugins), str(sample), cwd=tmp_path)
    assert result.stdout.strip() == (
        "orders.tsv: 1 item, total $15.00 via example:orders-tsv"
    )


def test_external_importer_needs_no_host_edits(tmp_path: Path) -> None:
    plugin = tmp_path / "plugins" / "tsv"
    plugin.mkdir(parents=True)
    (plugin / "manifest.json").write_text(
        json.dumps({
            "manifest_version": 1,
            "id": "example:orders-tsv",
            "version": "1.0.0",
            "source": {"code_path": "plugin.py", "class_name": "TsvOrdersPlugin"},
        }),
        encoding="utf-8",
    )
    (plugin / "plugin.py").write_text(
        "import csv\n"
        "from invariant.spec import BasePlugin\n"
        "class TsvOrdersPlugin(BasePlugin):\n"
        "    def initialize(self, **deps): pass\n"
        "    def shutdown(self): pass\n"
        "    def load(self, path):\n"
        "        with open(path, newline='', encoding='utf-8') as stream:\n"
        "            return list(csv.DictReader(stream, delimiter='\\t'))\n",
        encoding="utf-8",
    )
    file = tmp_path / "orders.tsv"
    file.write_text("item\tquantity\tunit_price\nWidget\t2\t7.50\n", encoding="utf-8")
    result = _run("--plugin-dir", str(plugin.parent), str(file), cwd=tmp_path)
    assert result.stdout.strip() == (
        "orders.tsv: 1 item, total $15.00 via example:orders-tsv"
    )
