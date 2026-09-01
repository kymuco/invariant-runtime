from __future__ import annotations

from pathlib import Path

from invariant.api import PluginService
from invariant.catalog.registry import PluginRegistry
from invariant.load.env.env_manager import EnvManager
from invariant.load.loader import PluginLoader


def test_service_adopts_loader_environment_manager(tmp_path: Path) -> None:
    manager = EnvManager(home=tmp_path / "runtime-home")
    loader = PluginLoader([], env_manager=manager)
    service = PluginService(registry=PluginRegistry(mode="strict"), loader=loader)
    try:
        assert service.env_manager is manager
        assert service.env_manager is loader._env_manager  # noqa: SLF001
    finally:
        service.close()


def test_manifest_constructor_shares_environment_manager_with_source() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    service = PluginService.from_manifest_paths([repo_root / "examples" / "hello_plugin"])
    try:
        assert service.env_manager is service.loader._env_manager  # noqa: SLF001
        manifest_sources = [
            source
            for source in service.loader.sources
            if getattr(source, "kind", None) == "manifest"
        ]
        assert len(manifest_sources) == 1
        assert manifest_sources[0].env_manager is service.env_manager
    finally:
        service.close()
