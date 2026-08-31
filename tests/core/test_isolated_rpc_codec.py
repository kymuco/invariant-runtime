from __future__ import annotations

import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path

from invariant.load.env.env_manager import EnvInfo
from invariant.load.isolated_proxy import IsolatedPluginProxy
from invariant.load.rpc_codec import decode_rpc_value, encode_rpc_value
from invariant.spec.descriptor import PluginDescriptor
from invariant.spec.source import PluginSourceSpec
from tests.rpc_contracts import RpcMarker, RpcRequest, RpcResult, RpcTone


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
        return EnvInfo(
            env_dir=plugin_root,
            python_exe=Path(sys.executable),
            key=f"fake:{plugin_key}",
        )


def test_rpc_codec_roundtrips_generic_contracts() -> None:
    request = RpcRequest(
        text="hello",
        tone=RpcTone.WARM,
        metadata={"channel": "smoke"},
    )
    result = RpcResult(
        value="HELLO",
        markers=(RpcMarker(offset=0, label="word"),),
        metadata={"cached": False},
    )

    decoded_request = decode_rpc_value(encode_rpc_value(request))
    decoded_result = decode_rpc_value(encode_rpc_value(result))

    assert isinstance(decoded_request, RpcRequest)
    assert decoded_request.tone is RpcTone.WARM
    assert decoded_request.metadata == {"channel": "smoke"}

    assert isinstance(decoded_result, RpcResult)
    assert decoded_result.value == "HELLO"
    assert decoded_result.markers[0].label == "word"


def test_isolated_proxy_roundtrips_generic_contract_payloads(tmp_path: Path) -> None:
    descriptor = _make_contract_descriptor(tmp_path)
    proxy = IsolatedPluginProxy(
        descriptor=descriptor,
        env_manager=_FakeEnvManager(),  # type: ignore[arg-type]
    )

    request = RpcRequest(
        text="hello",
        tone=RpcTone.WARM,
        metadata={"source": "unit"},
    )

    try:
        echoed = proxy.call("echo_request", request, timeout_s=1.0)
        assert isinstance(echoed, RpcRequest)
        assert echoed.text == "hello"
        assert echoed.tone is RpcTone.WARM

        produced = proxy.call("make_result", request, timeout_s=1.0)
        assert isinstance(produced, RpcResult)
        assert produced.value == "HELLO"
        assert produced.markers[0].label == "word"
        assert produced.metadata["tone"] == "warm"
    finally:
        proxy.close()


def _make_contract_descriptor(tmp_path: Path) -> PluginDescriptor:
    plugin_root = tmp_path / "contracts_plugin"
    plugin_root.mkdir(parents=True, exist_ok=True)

    code = textwrap.dedent(
        """
        from tests.rpc_contracts import RpcMarker, RpcResult


        class ContractRoundtripPlugin:
            def echo_request(self, request):
                return request

            def make_result(self, request):
                return RpcResult(
                    value=request.text.upper(),
                    markers=(
                        RpcMarker(offset=0, label="word"),
                    ),
                    metadata={
                        "tone": request.tone.value,
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
