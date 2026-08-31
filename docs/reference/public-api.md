# Public API reference

Invariant 0.1 keeps its supported compatibility surface deliberately narrow so host and plugin code have clear imports while internal runtime layers can still evolve.

## Stability level

This is an **alpha / 0.x compatibility contract**, not a 1.0 stability guarantee.

Public facade changes should be intentional and reviewed as compatibility changes. Internal modules may evolve more quickly.

## Package metadata

| Symbol | Purpose |
| --- | --- |
| `invariant.__version__` | Installed `invariant-runtime` distribution version. |

```python
from invariant import __version__
```

The root package intentionally does not re-export runtime classes.

## Host-facing API: `invariant.api`

Applications embedding Invariant should import from `invariant.api`.

| Export | Purpose |
| --- | --- |
| `PluginService` | High-level host facade for plugin activation and scoped execution. |
| `BasePolicy` | Base policy contract. |
| `ManagedPolicy` | Policy for managed service-owned plugin instances. |
| `EphemeralPolicy` | Policy for execution-scoped runtime state. |
| `SandboxLimits` | Capability and resource limits for governed execution. |
| `WorkspaceOptions` | Persistent workspace limits and path configuration. |
| `PolicyProfile` | Named policy profile type. |
| `normalize_profile` | Normalize a profile name. |
| `profile_from_env` | Resolve a profile from environment configuration. |
| `sandbox_limits_for_profile` | Build limits for a named profile. |
| `ephemeral_policy_for_profile` | Build an ephemeral policy for a named profile. |

### `PluginService.from_manifest_paths(...)`

Recommended constructor for manifest-based hosts:

```python
from invariant.api import PluginService

service = PluginService.from_manifest_paths(
    ["plugins"],
    include_unstable=False,
    fail_fast=True,
)
```

| Parameter | Default | Meaning |
| --- | --- | --- |
| `search_paths` | required | One or more directories recursively searched for `manifest.json`. |
| `include_unstable` | `False` | Include plugins whose maturity is not `stable`. |
| `fail_fast` | `True` | Raise on discovery errors instead of logging and skipping them. |
| `tracer` | `None` | Optional runtime tracer integration. |

Discovery is strict: every discovered manifest plugin must declare an explicit version.

### `service.get(plugin_id)`

Returns a managed plugin instance.

```python
plugin = service.get("example:hello")
plugin = service.get("example:hello@1.0.0")
```

An unpinned ID selects the highest registered non-local concrete version.

### `service.shutdown(plugin_id)`

Shuts down one managed plugin and removes its runtime-owned managed instance.

### `service.close()`

Performs best-effort shutdown of service-owned managed runtime state. Hosts should call it during application shutdown.

### `service.execution(...)`

Creates an execution-scoped context using an explicit policy and execution-owned runtime state.

### `service.workspace(...)`

Creates an execution context bound to a persistent workspace ID.

For policy and capability semantics, read [Security](../security.md) and [Capabilities](capabilities.md).

## Plugin-author API: `invariant.spec`

Plugin implementations and plugin-side tooling should import from `invariant.spec`.

| Export | Purpose |
| --- | --- |
| `BasePlugin` | Base lifecycle contract for concrete plugins. |
| `PluginContext` | Protocol for runtime-provided plugin context information. |
| `PluginMeta` | Immutable plugin metadata. |
| `DependencySpec` | Declarative plugin/capability dependency. |
| `DependencyScope` | Dependency lifetime enum. |
| `Version` | Invariant-owned concrete version parser and comparator. |
| `InvalidVersion` | Error raised for invalid concrete versions. |
| `canon_version_key` | Normalize a concrete version to its registry key. |

### `BasePlugin`

Concrete plugins implement:

- `initialize(**deps)`
- `shutdown()`

See [Plugin authoring](../guides/plugin-authoring.md) for lifecycle guidance.

### `PluginMeta`

Immutable metadata including ID, version, dependencies, categories, tags, author, maturity, repository metadata, and priority.

Manifest-based plugins normally declare these fields in `manifest.json` rather than constructing `PluginMeta` directly.

### `DependencySpec` and `DependencyScope`

A dependency declares:

- `id`
- `param`
- `scope`
- `optional`

Available scopes are `SINGLETON`, `EXECUTION`, `PLUGIN`, and `TRANSIENT`.

### `PluginContext`

Protocol for runtime-provided context information such as runtime ID, configuration mapping, and event emission.

### `Version`

```python
from invariant.spec import Version

assert Version("1.0rc1") < Version("1.0")
```

### `canon_version_key(...)`

```python
from invariant.spec import canon_version_key

assert canon_version_key("1.2") == "1.2.0"
```

## Implementation-level modules

These modules are **not** part of the explicit 0.1 compatibility facade:

```text
invariant.catalog
invariant.load
invariant.exec
invariant.ops
```

Unexported objects inside `invariant.api` and `invariant.spec` are also implementation-level unless a future compatibility contract says otherwise.

Advanced integrations may inspect or use those modules, but direct imports accept a higher compatibility risk during the 0.x line.

## Typing

Invariant ships a `py.typed` marker and inline Python annotations. Type checkers can consume the installed package directly.

## Compatibility rules for 0.x

- new public exports should be added deliberately;
- removal or semantic changes to an existing public export should be called out in release notes;
- implementation-level modules may change without the same compatibility promise;
- examples and ordinary guides should use the public facades unless they are explicitly documenting internals.

## See also

- [Getting started](../getting-started.md)
- [Plugin authoring](../guides/plugin-authoring.md)
- [Manifest v1](manifest-v1.md)
