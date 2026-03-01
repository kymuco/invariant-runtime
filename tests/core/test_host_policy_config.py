from __future__ import annotations

import pytest

from invariant.ops.errors import InvalidCommand
from invariant.ops.host import RuntimeHost
from tests.fake_service import FakePluginService


class _ConfigAwareService(FakePluginService):
    def policy_config_for_profile(self, profile: str) -> dict[str, object]:
        assert profile in {"dev", "stage", "prod"}
        return {"quality_gate.max_checks": 7, "run_factory.approval.ttl_s": 600}


class _BadConfigService(FakePluginService):
    def policy_config_for_profile(self, _profile: str) -> str:
        return "not-a-mapping"


def test_runtime_host_injects_policy_profile_config_into_policies():
    host = RuntimeHost(
        plugin_service=_ConfigAwareService(),
        enable_watchdog=False,
        policy_profile="dev",
    )
    try:
        assert host._policies["ephemeral"].config["quality_gate.max_checks"] == 7  # noqa: SLF001
        assert host._policies["managed"].config["run_factory.approval.ttl_s"] == 600  # noqa: SLF001
    finally:
        host.close()


def test_runtime_host_rejects_non_mapping_profile_config():
    with pytest.raises(InvalidCommand):
        RuntimeHost(
            plugin_service=_BadConfigService(),
            enable_watchdog=False,
            policy_profile="dev",
        )
