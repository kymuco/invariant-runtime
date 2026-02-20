from __future__ import annotations

import pytest

from invariant.api.policy_profiles import (
    ephemeral_policy_for_profile,
    normalize_profile,
    profile_from_env,
    sandbox_limits_for_profile,
)


def test_normalize_profile_accepts_known_values() -> None:
    assert normalize_profile("dev") == "dev"
    assert normalize_profile("STAGE") == "stage"
    assert normalize_profile("prod") == "prod"


def test_normalize_profile_rejects_unknown() -> None:
    with pytest.raises(ValueError):
        normalize_profile("qa")


def test_profile_from_env(monkeypatch) -> None:
    monkeypatch.setenv("INVARIANT_PROFILE", "stage")
    assert profile_from_env() == "stage"


def test_sandbox_limits_for_profile_defaults() -> None:
    dev = sandbox_limits_for_profile("dev")
    stage = sandbox_limits_for_profile("stage")
    prod = sandbox_limits_for_profile("prod")

    assert dev.allow_fs_delete is True
    assert stage.allow_fs_delete is False
    assert prod.allow_fs_delete is False
    assert dev.allow_env_config is True
    assert stage.allow_env_config is False
    assert prod.allow_env_config is False
    assert dev.allow_network is False
    assert stage.allow_network is False
    assert prod.allow_network is False
    assert dev.http_allow_any_host is False
    assert stage.http_allow_any_host is False
    assert prod.http_allow_any_host is False
    assert dev.http_allow_hosts == ()
    assert stage.http_allow_hosts == ()
    assert prod.http_allow_hosts == ()


def test_sandbox_limits_for_profile_overrides() -> None:
    limits = sandbox_limits_for_profile(
        "prod",
        overrides={
            "allow_network": True,
            "http_allow_hosts": ["api.openai.com", "example.com"],
            "allow_env_config": True,
            "config_env_prefix": "APP_",
        },
    )
    assert limits.allow_network is True
    assert limits.http_allow_hosts == ("api.openai.com", "example.com")
    assert limits.allow_env_config is True
    assert limits.config_env_prefix == "APP_"


def test_sandbox_limits_for_profile_unknown_override_key() -> None:
    with pytest.raises(ValueError):
        sandbox_limits_for_profile("dev", overrides={"nope": 1})


def test_ephemeral_policy_for_profile_returns_policy() -> None:
    p = ephemeral_policy_for_profile("stage", config={"app.name": "Invariant"})
    assert p.limits.allow_fs_write is True
    assert p.limits.allow_fs_delete is False
    assert p.config["app.name"] == "Invariant"
