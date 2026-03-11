from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import pytest

from invariant.spec.meta import PluginMeta
from plugin_packs.core.config_plugin.config_plugin import ConfigPlugin


class _DictSource:
    def __init__(self, data: dict[str, Any]):
        self._data = dict(data)

    def get(self, key: str) -> Any:
        if key not in self._data:
            raise KeyError(key)
        return self._data[key]

    def list_keys(self, prefix: str = "") -> Iterable[str]:
        for key in sorted(self._data):
            if not prefix or key.startswith(prefix):
                yield key


def test_config_plugin_section_strips_prefix_by_default() -> None:
    plugin = _build_plugin(
        {
            "character_os.tts.asset_root": "E:/assets/lina_v1",
            "character_os.tts.voice_id": "lina",
            "character_os.avatar.overlay_url": "http://127.0.0.1:8876",
        }
    )

    assert plugin.section("character_os.tts.") == {
        "asset_root": "E:/assets/lina_v1",
        "voice_id": "lina",
    }
    assert plugin.export(prefix="character_os.tts.") == {
        "character_os.tts.asset_root": "E:/assets/lina_v1",
        "character_os.tts.voice_id": "lina",
    }


def test_config_plugin_require_section_raises_for_missing_prefix() -> None:
    plugin = _build_plugin({"character_os.llm.model": "qwen3.5:2b"})

    with pytest.raises(KeyError):
        plugin.require_section("character_os.tts.")


def test_config_plugin_section_can_mask_secrets() -> None:
    plugin = _build_plugin(
        {
            "character_os.tts.api_token": "secret-token-value",
            "character_os.tts.asset_root": "E:/assets/lina_v1",
        }
    )

    assert plugin.section("character_os.tts.", mask_secrets=True) == {
        "api_token": "se...ue",
        "asset_root": "E:/assets/lina_v1",
    }


def test_config_plugin_typed_getters_coerce_scalar_values() -> None:
    plugin = _build_plugin(
        {
            "character_os.tts.timeout_s": "12",
            "character_os.tts.temperature": "0.75",
            "character_os.tts.enabled": "yes",
            "character_os.avatar.visible": 1,
            "character_os.character.language": "ru",
        }
    )

    assert plugin.require_int("character_os.tts.timeout_s") == 12
    assert plugin.require_float("character_os.tts.temperature") == pytest.approx(0.75)
    assert plugin.require_bool("character_os.tts.enabled") is True
    assert plugin.require_bool("character_os.avatar.visible") is True
    assert plugin.require_str("character_os.character.language") == "ru"


def test_config_plugin_typed_getters_use_defaults_only_for_missing_keys() -> None:
    plugin = _build_plugin({"character_os.tts.timeout_s": "9"})

    assert plugin.get_int("character_os.tts.missing_timeout", default=30) == 30
    assert plugin.get_bool("character_os.tts.missing_enabled", default=False) is False

    with pytest.raises(TypeError):
        plugin.get_bool("character_os.tts.timeout_s", default=True)


def test_config_plugin_typed_getters_raise_on_bad_values() -> None:
    plugin = _build_plugin(
        {
            "character_os.tts.timeout_s": "abc",
            "character_os.tts.enabled": "maybe",
            "character_os.character.language": 42,
        }
    )

    with pytest.raises(TypeError):
        plugin.require_int("character_os.tts.timeout_s")
    with pytest.raises(TypeError):
        plugin.require_bool("character_os.tts.enabled")
    with pytest.raises(TypeError):
        plugin.require_str("character_os.character.language")
    with pytest.raises(KeyError):
        plugin.require_float("character_os.tts.temperature")


def _build_plugin(data: dict[str, Any]) -> ConfigPlugin:
    plugin = ConfigPlugin(meta=PluginMeta(id="invariant:config", version="0.1.0"))
    plugin.initialize(source=_DictSource(data))
    return plugin
