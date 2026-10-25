"""Source-independent wheel smoke for the staged 0.1.2a1 alpha candidate.

Run with the isolated wheel venv's Python -I from OUTSIDE the checkout.
This script is not packaged in the wheel or imported by runtime users.
"""
from __future__ import annotations

import importlib.metadata
import json
import re
import sys
import zipfile
from importlib.resources import files
from pathlib import Path
from tempfile import TemporaryDirectory

import invariant
from invariant.api import PluginService
from invariant.load.acquisition import install_verified_https_plugin
from invariant.load.catalog_update import CatalogUpdateJournal
from invariant.load.installation_plan import PluginInstallationPlanner
from invariant.load.plugin_catalog import VerifiedPluginCatalog
from invariant.load.plugin_store import LocalPluginStore
from invariant.load.sources.installed import InstalledSource
from invariant.spec import PROCESS_CAPABILITY

EXPECTED_VERSION = "0.1.2a1"


def main() -> None:
    assert sys.prefix != sys.base_prefix, "isolated venv required"
    package_file = Path(invariant.__file__).resolve()
    environment = Path(sys.prefix).resolve()
    assert package_file.is_relative_to(environment), package_file
    assert "site-packages" in package_file.parts, package_file

    dist = importlib.metadata.distribution("invariant-runtime")
    actual_version = dist.version
    assert actual_version == EXPECTED_VERSION, actual_version
    assert invariant.__version__ == EXPECTED_VERSION, invariant.__version__
    # PEP 508 Requires-Dist includes optional extras: pytest for [test] and
    # Zensical for [docs]. Only unguarded dependencies are runtime deps.
    requirements = dist.requires or []
    optional_marker = re.compile(
        r"""extra\s*==\s*(['"])(test|docs)\1"""
    )
    required_at_runtime = [
        requirement
        for requirement in requirements
        if ";" not in requirement
        or optional_marker.fullmatch(requirement.split(";", 1)[1].strip())
        is None
    ]
    assert not required_at_runtime, (
        "runtime wheel has mandatory dependencies: " + repr(required_at_runtime)
    )
    assert files("invariant").joinpath("py.typed").is_file()
    assert PROCESS_CAPABILITY.ref == "invariant:capability.process@0.2.0"

    # Self-contained manifest + plugin live OUTSIDE the source tree and
    # wheel. Only the installed public host and plugin contracts are used.
    with TemporaryDirectory(prefix="invariant-alpha-wheel-") as temp_dir:
        root = Path(temp_dir)
        plugin = root / "alpha_plugin"
        plugin.mkdir()
        (plugin / "manifest.json").write_text(
            json.dumps(
                {
                    "manifest_version": 1,
                    "id": "release:alpha_smoke",
                    "version": "1.0.0",
                    "source": {
                        "code_path": "plugin.py",
                        "class_name": "HelloPlugin",
                    },
                }
            ),
            encoding="utf-8",
        )
        (plugin / "plugin.py").write_text(
            "from invariant.spec import BasePlugin\n"
            "\n"
            "class HelloPlugin(BasePlugin):\n"
            "    def initialize(self, **deps):\n"
            "        return None\n"
            "    def shutdown(self):\n"
            "        return None\n"
            "    def greet(self, name):\n"
            "        return f'Hello, {name}!'\n",
            encoding="utf-8",
        )

        service = PluginService.from_manifest_paths([root])
        try:
            listed = service.list_plugins()
            assert any(x.id == "release:alpha_smoke" for x in listed)
            assert service.get("release:alpha_smoke").greet("alpha") == (
                "Hello, alpha!"
            )
        finally:
            service.close()

        # Installed-wheel proof of the internal ZIP → Store → InstalledSource path.
        # The archive is deleted after installation; there is no network access.
        archive = root / "alpha-plugin.zip"
        with zipfile.ZipFile(archive, "w") as zipped:
            zipped.write(plugin / "manifest.json", arcname="manifest.json")
            zipped.write(plugin / "plugin.py", arcname="plugin.py")
        store = LocalPluginStore(root / "installed-store")
        store.install_zip(archive)
        archive.unlink()
        with store.open_service() as installed_service:
            assert len(installed_service.loader.sources) == 1
            assert isinstance(installed_service.loader.sources[0], InstalledSource)
            assert installed_service.get("release:alpha_smoke@1.0.0").greet("store") == (
                "Hello, store!"
            )
        assert len(list(store.objects.iterdir())) == 1
        assert not list(store.leases.iterdir())

    print(
        "INVARIANT_ALPHA_WHEEL_PROOF_JSON="
        + json.dumps(
            {
                "schema": "INVARIANT_ALPHA_INSTALLED_WHEEL_V1",
                "version": actual_version,
                "import_path": str(package_file),
                "dependencies": 0,
                "py_typed": True,
                "manifest_activation": "PASS",
                "installed_zip_activation": "PASS",
                "internal_delivery_modules": all((
                    LocalPluginStore, InstalledSource, install_verified_https_plugin,
                    PluginInstallationPlanner, VerifiedPluginCatalog, CatalogUpdateJournal,
                )),
                "status": "PASS",
            },
            sort_keys=True,
        )
    )
    print("INVARIANT_ALPHA_INSTALLED_WHEEL_PASS")


if __name__ == "__main__":
    main()
