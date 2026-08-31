# Core

## Purpose

`invariant/` is the neutral runtime layer for plugins:

- lifecycle
- dependency resolution
- policy-gated capabilities
- execution and workspace flows
- source loading and isolated workers
- host and ops infrastructure

Core must stay product-agnostic. Product workflows and domain behavior belong outside this repository.

## Boundaries

- `invariant/`
  reusable runtime, capability, loading, execution, and API contracts
- `tests/core/`
  standalone runtime regression suite
- downstream repositories/packages
  domain plugins, product assemblies, agents, companions, workflows, marketplace systems, and application UX

Rule of thumb:
If logic is reusable across arbitrary plugin-based products, it may belong in `invariant/`.
If logic encodes one product's domain behavior, orchestration policy, identity, or UX, it belongs downstream.

## Core package map

- `invariant/spec/`
  IDs, descriptors, manifests, dependencies, errors, concrete-version semantics
- `invariant/load/`
  discovery, source loading, plugin environments, isolated proxy and RPC codec
- `invariant/catalog/`
  descriptor registry and version resolution
- `invariant/exec/`
  runtime activation, dependency wiring, transactional execution, capabilities
- `invariant/api/`
  `PluginService`, policy primitives, profile helpers
- `invariant/ops/`
  daemon, host, supervisor, tracing, metrics, worker, watchdog

## Dependency boundary

Production code under `invariant/` is standard-library-only. It may import Python stdlib modules and other `invariant` modules, but no mandatory third-party runtime package.

Tests and build tooling may have development-time dependencies. The core suite contains a static import regression that enforces the production boundary.

## Execution lifecycle

1. `PluginLoader.discover(...)` builds descriptors.
2. `PluginRegistry.register(...)` stores and normalizes descriptors.
3. `DependencyResolver` builds the dependency graph.
4. `ExecutionRuntime` activates plugins transactionally.
5. `PluginService.execution(...)` or `PluginService.workspace(...)` provides scoped access.
6. Commit, rollback, and shutdown close the runtime consistently.

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

In-process plugins are trusted code. Isolated workers are useful process boundaries but are not a sufficient hostile-code sandbox. Untrusted plugins require an external isolation layer.

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

## Versioning

Invariant owns concrete plugin-version parsing and ordering in `invariant.spec.versioning` / `invariant.spec._version`.

The concrete version type supports numeric releases, epochs, prereleases, post releases, dev releases, and local labels. Registry keys pad release versions to at least three components, so `1`, `1.0`, and `1.0.0` resolve to the same canonical key.

Version ranges/specifier expressions are deliberately outside this concrete-version type.

## Source status

- `ManifestSource` and `FilesystemSource` are implemented.
- `EntrypointSource`, `ZipSource`, and `RemoteSource` remain incomplete/stubbed.

## Deliberate exclusions

The following do not belong in the Invariant core:

- agent/task/companion contracts
- product-specific orchestration
- domain plugin implementations
- marketplace billing, entitlement, ranking, or distribution control planes
- product UI and application state
