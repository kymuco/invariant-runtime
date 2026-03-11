# Core

## Purpose

`invariant/` is the neutral runtime layer for plugins:

- lifecycle
- dependency resolution
- policy-gated capabilities
- execution and workspace flows
- ops infrastructure

Core must stay product-agnostic. Product workflows belong outside the core.

## Boundaries

- `invariant/`
  reusable runtime and API contracts
- `plugin_packs/`
  plugin manifests and implementations
- `apps/character_os/`
  current product assembly

Rule of thumb:
If logic is reusable across any plugin-based product, keep it in `invariant/`.
If logic is specific to Character OS behavior, keep it outside the core.

## Core package map

- `invariant/spec/`
  ids, descriptors, manifests, error model
- `invariant/load/`
  discovery and source loading
- `invariant/catalog/`
  descriptor and version registry
- `invariant/exec/`
  runtime activation, dependency wiring, capabilities
- `invariant/api/`
  `PluginService`, policy primitives, profile helpers
- `invariant/ops/`
  daemon, host, supervisor, tracing, metrics

## Execution lifecycle

1. `PluginLoader.discover(...)` builds descriptors.
2. `PluginRegistry.register(...)` stores descriptors.
3. `DependencyResolver` builds the dependency graph.
4. `ExecutionRuntime` activates plugins transactionally.
5. `PluginService.execution(...)` or `PluginService.workspace(...)` provides scoped access.
6. Commit, rollback, and shutdown close runtime consistently.

## Capabilities and security model

Capabilities are policy and API boundaries, not a full sandbox boundary.

Common capability IDs:

- `invariant:capability.config_source`
- `invariant:capability.inspector`
- `invariant:capability.fs_sandbox`
- `invariant:capability.workspace`
- `invariant:capability.http`
- `invariant:capability.kv`

Capability contract details live in `CAPABILITIES.md`.

## Runtime modes

- `service.get(...)`
  managed singleton path
- `service.execution(...)`
  execution-scoped runtime
- `service.workspace(...)`
  execution with persistent workspace root

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

- `ManifestSource` and `FilesystemSource` are working
- `EntrypointSource`, `ZipSource`, and `RemoteSource` are still stubs

## Deliberate exclusions

- agent and task contracts do not belong in `invariant/spec`
- product-specific orchestration belongs in `apps/character_os/`
- domain plugins belong in `plugin_packs/character_os/`
