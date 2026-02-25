# Core (Current)

## Purpose

`invariant/` is the neutral runtime layer for plugins:

- lifecycle (discover/load/activate/shutdown),
- dependency resolution,
- policy-gated capabilities,
- execution/managed/workspace flows,
- ops infrastructure (host/supervisor/daemon/tracing/metrics).

Core must stay product-agnostic. Agent-specific workflows belong to `apps/agent_factory/`.

## Boundaries

- `invariant/`:
  reusable runtime and API contracts.
- `plugin_packs/`:
  plugin manifests and implementations (domain extensions).
- `apps/agent_factory/`:
  product assembly (boot + CLI + presets/policies).

Rule of thumb:
If logic is reusable across any plugin-based product, keep it in `invariant/`.
If logic is specific to Agent Factory workflow, keep it in `apps/agent_factory/`.

## Core package map

- `invariant/spec/`
  contracts, ids, error model, source metadata.
- `invariant/load/`
  discovery and loading from configured sources.
- `invariant/catalog/`
  descriptor/version registry (`PluginRegistry`).
- `invariant/exec/`
  dependency graph, lifecycle kernel, activation transaction, capability wiring.
- `invariant/api/`
  `PluginService`, policy primitives, profile helpers.
- `invariant/ops/`
  daemon/host/supervisor, worker protocol, tracing/metrics, watchdog.

## Execution lifecycle

1. `PluginLoader.discover(...)` builds `PluginDescriptor` list.
2. `PluginRegistry.register(...)` stores descriptors.
3. `DependencyResolver` builds dependency graph.
4. `ExecutionRuntime` activates plugins in transaction.
5. `PluginService.execution(...)` / `PluginService.workspace(...)` provides scoped context.
6. Commit/Rollback/Shutdown closes runtime consistently.

## Capabilities and security model

Capabilities are policy/API boundaries, not a full sandbox boundary.

Common capability IDs:

- `invariant:capability.config_source`
- `invariant:capability.inspector`
- `invariant:capability.fs_sandbox`
- `invariant:capability.workspace`
- `invariant:capability.http` (optional)
- `invariant:capability.kv` (optional)

Capability contract details: `CAPABILITIES.md`.

## Runtime modes

- `service.get(...)`:
  managed singleton path.
- `service.execution(...)`:
  execution-scoped runtime.
- `service.workspace(...)`:
  execution with persistent workspace root.

## Policy profiles

Core supports standard execution profiles:

- `dev`
- `stage`
- `prod`

API:

- `invariant.api.policy_profiles.profile_from_env`
- `invariant.api.policy_profiles.ephemeral_policy_for_profile`
- `invariant.api.policy_profiles.sandbox_limits_for_profile`

## Source status

- `ManifestSource`, `FilesystemSource` are working.
- `EntrypointSource`, `ZipSource`, `RemoteSource` are currently stubs.

## Out of core scope

- Agent Factory boot assembly: `apps/agent_factory/bootstrap/service_factory.py`
- Agent Factory CLI: `apps/agent_factory/cli/main.py`
- Domain plugin packs: `plugin_packs/**`

These parts evolve faster than core and should not force core-level breaking changes without clear reason.
