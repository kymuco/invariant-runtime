from __future__ import annotations

import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path

from apps.character_os.contracts import (
    ReplyEmotion,
    SpeechMarker,
    SpeechMarkerKind,
    SpeechRequest,
    SpeechResult,
    SpeechStyle,
)
from invariant.load.env.env_manager import EnvInfo
from invariant.load.isolated_proxy import IsolatedPluginProxy
from invariant.load.rpc_codec import decode_rpc_value, encode_rpc_value
from invariant.spec.descriptor import PluginDescriptor
from invariant.spec.source import PluginSourceSpec


@dataclass(frozen=True, slots=True)
class _MiniMeta:
    id: str
    version: str | None = "0.1.0"
    name: str | None = None
    description: str | None = None
    author: str | None = None
    repo: str | None = None
    maturity: str = "stable"
    priority: int = 0


class _FakeEnvManager:
    def ensure_env(self, *, plugin_key: str, plugin_root: Path, deps) -> EnvInfo:  # noqa: ANN001
        return EnvInfo(env_dir=plugin_root, python_exe=Path(sys.executable), key=f"fake:{plugin_key}")


def test_rpc_codec_roundtrips_character_contracts() -> None:
    request = SpeechRequest(
        text="Привет",
        voice_id="lina",
        style=SpeechStyle.SOFT,
        emotion=ReplyEmotion.WARM,
        speed=1.15,
        metadata={"channel": "smoke"},
    )
    result = SpeechResult(
        audio_ref="E:/tmp/out.wav",
        duration_ms=420,
        sample_rate_hz=24000,
        markers=(
            SpeechMarker(time_ms=0, kind=SpeechMarkerKind.WORD, value="Привет"),
        ),
        provider="tts_sherpa",
        metadata={"cached": False},
    )

    decoded_request = decode_rpc_value(encode_rpc_value(request))
    decoded_result = decode_rpc_value(encode_rpc_value(result))

    assert isinstance(decoded_request, SpeechRequest)
    assert decoded_request.style is SpeechStyle.SOFT
    assert decoded_request.emotion is ReplyEmotion.WARM
    assert decoded_request.metadata == {"channel": "smoke"}

    assert isinstance(decoded_result, SpeechResult)
    assert decoded_result.provider == "tts_sherpa"
    assert len(decoded_result.markers) == 1
    assert decoded_result.markers[0].kind is SpeechMarkerKind.WORD


def test_isolated_proxy_roundtrips_character_contract_payloads(tmp_path: Path) -> None:
    descriptor = _make_contract_descriptor(tmp_path)
    proxy = IsolatedPluginProxy(descriptor=descriptor, env_manager=_FakeEnvManager())  # type: ignore[arg-type]
    request = SpeechRequest(
        text="Привет",
        voice_id="lina",
        style=SpeechStyle.SOFT,
        emotion=ReplyEmotion.SUPPORTIVE,
        speed=1.0,
        metadata={"source": "unit"},
    )
    try:
        echoed = proxy.call("echo_request", request, timeout_s=1.0)
        assert isinstance(echoed, SpeechRequest)
        assert echoed.voice_id == "lina"
        assert echoed.style is SpeechStyle.SOFT
        assert echoed.emotion is ReplyEmotion.SUPPORTIVE

        synthesized = proxy.call("make_result", request, timeout_s=1.0)
        assert isinstance(synthesized, SpeechResult)
        assert synthesized.audio_ref.endswith("fake.wav")
        assert synthesized.markers[0].kind is SpeechMarkerKind.WORD
        assert synthesized.metadata["voice_id"] == "lina"
    finally:
        proxy.close()


def _make_contract_descriptor(tmp_path: Path) -> PluginDescriptor:
    plugin_root = tmp_path / "contracts_plugin"
    plugin_root.mkdir(parents=True, exist_ok=True)
    code = textwrap.dedent(
        """
        from apps.character_os.contracts import SpeechMarker, SpeechMarkerKind, SpeechResult


        class ContractRoundtripPlugin:
            def echo_request(self, request):
                return request

            def make_result(self, request):
                return SpeechResult(
                    audio_ref="E:/tmp/fake.wav",
                    duration_ms=111,
                    sample_rate_hz=24000,
                    markers=(
                        SpeechMarker(time_ms=0, kind=SpeechMarkerKind.WORD, value=request.text),
                    ),
                    provider="test_worker",
                    metadata={
                        "voice_id": request.voice_id,
                        "emotion": request.emotion.value,
                    },
                )
        """
    ).strip()
    (plugin_root / "plugin.py").write_text(code, encoding="utf-8")
    (plugin_root / "requirements.txt").write_text("", encoding="utf-8")

    source = PluginSourceSpec(
        kind="manifest",
        path=(plugin_root / "plugin.py"),
        module=None,
        class_name="ContractRoundtripPlugin",
        extra={
            "plugin_root": str(plugin_root),
            "code_rel": "plugin.py",
            "deps": {
                "mode": "online",
                "requirements": "requirements.txt",
                "index_url": None,
                "extra_index_urls": [],
                "trusted_hosts": [],
                "only_binary": True,
            },
            "call_timeout_s": 1.0,
        },
    )
    return PluginDescriptor(
        meta=_MiniMeta(id="tests:contract_roundtrip"),  # type: ignore[arg-type]
        source=source,
    )
