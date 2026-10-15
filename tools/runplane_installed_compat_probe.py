"""Independent installed wheel compatibility proof, not a production entrypoint.

Invoke with venv/python -I from a DIFFERENT directory than the two source
trees. This checks genuine Runplane effect dispatch and its Invariant host
execution path without changing either repository's packaging metadata.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory


def installed_import(name: str, environment: Path) -> tuple[str, str]:
    module = __import__(name)
    module_file = Path(module.__file__).resolve()
    # Both modules must come from the isolated proof venv, never a checkout.
    try:
        module_file.relative_to(environment)
    except ValueError as exc:
        raise AssertionError(
            f"{name} imported outside the isolated environment: {module_file}"
        ) from exc
    if "site-packages" not in module_file.parts:
        raise AssertionError(f"{name} is not installed in site-packages: {module_file}")
    return str(module_file), importlib.metadata.version(
        "invariant-runtime" if name == "invariant" else "runplane"
    )


def sdk_effect_smoke(workspace: Path) -> tuple[int, int]:
    from runplane import Runplane

    with Runplane.create() as sdk:
        # The real separately installed consumer's plugin manifest inventory.
        plugins = sdk.service.list_plugins()
        ids = {item.id for item in plugins}
        required = {
            "invariant:config",
            "invariant:echo",
            "invariant:introspect",
            "invariant:schema_guard",
            "invariant:trace_sink",
        }
        if not required.issubset(ids):
            raise AssertionError(f"missing real Runplane bootstrap plugins: {required - ids}")

        echo = sdk.get("invariant:echo")
        assert echo.ping() == "pong", "real Runplane echo plugin not READY"
        effects = sdk.list_effects()
        assert len(effects) == 29, "canonical effect inventory changed"

        client = sdk.effect_client(repo=workspace)
        stat = client.execute_effect("workspace.path_stat", {"path": "fixture.txt"})
        if not stat.get("ok") or stat.get("status") != "completed":
            raise AssertionError(f"real installed path_stat failed: {stat}")
        if stat["result"].get("kind") != "file":
            raise AssertionError(f"path_stat did not observe a file: {stat}")

        read = client.execute_effect(
            "workspace.read_file",
            {"path": "fixture.txt", "max_bytes": 128},
        )
        if not read.get("ok") or read.get("status") != "completed":
            raise AssertionError(f"real installed read_file failed: {read}")
        if read["result"].get("content") != "installed-runplane-proof\n":
            raise AssertionError("unexpected real provider result from read_file")
        return len(ids), len(effects)


def real_consumer_host_lifecycle() -> None:
    # This is a diagnostic harness using Invariant's INTERNAL RuntimeHost.
    # It does not add internal imports to Runplane production code, and is
    # separate from the public SDK consumer proof above.
    from invariant.ops.host import RuntimeHost
    from runplane.bootstrap import build_service

    service = build_service()
    host = RuntimeHost(plugin_service=service, enable_watchdog=False)
    try:
        created = host.handle({"cmd": "create_execution", "policy": "ephemeral"})
        assert created.get("ok") is True, created
        execution_id = created["execution"]

        getter = host.handle(
            {
                "cmd": "execution_plugin_get",
                "execution": execution_id,
                "plugin": "invariant:echo",
            }
        )
        assert getter.get("ok") is True, getter
        invoked = host.handle(
            {
                "cmd": "execution_plugin_call",
                "execution": execution_id,
                "handle": getter["handle"],
                "method": "ping",
            }
        )
        assert invoked == {"ok": True, "result": "pong"}, invoked
        # The host now owns one LIVE Runplane plugin execution; closing it
        # must remove the execution without abandoning it.
        host.close()
        assert host._supervisor._executions == {}, "host leaked active execution"
        denied = host.handle({"cmd": "create_execution", "policy": "ephemeral"})
        assert denied.get("ok") is False and denied.get("error") == "closed", denied
        host.close()  # idempotent
    finally:
        host.close()


def main() -> None:
    if sys.prefix == sys.base_prefix:
        raise AssertionError("consumer proof MUST run inside a fresh venv")
    env = Path(sys.prefix).resolve()
    invariant_file, invariant_version = installed_import("invariant", env)
    runplane_file, runplane_version = installed_import("runplane", env)

    requires = importlib.metadata.requires("runplane") or []
    declared = [x for x in requires if x.startswith("invariant-runtime")]
    if not declared or "git+https://github.com/kymuco/invariant-runtime.git@" not in declared[0]:
        raise AssertionError(
            "Runplane no longer declares a pinned Git Invariant dependency; "
            "re-evaluate the explicit --no-deps compatibility proof"
        )
    if "3ef4166c3f9e24a6050c1076ad9af846f4f9af63" not in declared[0]:
        raise AssertionError("Runplane's expected historical dependency pin moved")

    with TemporaryDirectory(prefix="invariant-runplane-consumer-") as tmp:
        root = Path(tmp)
        fixture = root / "fixture.txt"
        content = b"installed-runplane-proof\n"
        fixture.write_bytes(content)
        expected = hashlib.sha256(content).hexdigest()

        plugin_count, effects_count = sdk_effect_smoke(root)
        real_consumer_host_lifecycle()

        if not fixture.is_file() or hashlib.sha256(fixture.read_bytes()).hexdigest() != expected:
            raise AssertionError("read-only Runplane effects unexpectedly mutated fixture")

    report = {
        "schema": "INVARIANT_RUNPLANE_INSTALLED_WHEEL_CONSUMER_V1",
        "pass": True,
        "runplane_version": runplane_version,
        "invariant_version": invariant_version,
        "runplane_import": runplane_file,
        "invariant_import": invariant_file,
        "plugins_discovered": plugin_count,
        "canonical_effects": effects_count,
        "historical_pin_bypassed_for_candidate": True,
        "consumer_scope": "real_installed_runplane_sdk_readonly_plus_real_plugin_host_close",
    }
    print("INVARIANT_RUNPLANE_WHEEL_PROOF_JSON=" + json.dumps(report, sort_keys=True))
    print("INVARIANT_RUNPLANE_INSTALLED_CONSUMER_PASS")


if __name__ == "__main__":
    main()
