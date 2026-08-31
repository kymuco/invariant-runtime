# Public API reference

Invariant 0.1 defines a deliberately narrow compatibility surface.

The goal is simple: application and plugin code should have clear supported imports while the runtime remains free to evolve its internal implementation.

## Package metadata

### `invariant.__version__`

```python
from invariant import __version__
```

Returns the installed `invariant-runtime` distribution version.

The root package intentionally does not re-export runtime classes.

## Host-facing API: `invariant.api`

Use this namespace from applications embedding Invariant.

```python
from invariant.api import (
    BasePolicy,
    EphemeralPolicy,
    ManagedPolicy,
    PluginService,
    PolicyProfile,
    SandboxLimits,
    WorkspaceOptions,
    ephemeral_policy_for_profile,
    normalize_profile,
    profile_from_env,
    sandbox_limits_for_profile,
)
```

### `PluginService`

High-level host facade for plugin activation and execution contexts.

#### `PluginService.from_manifest_paths(...)`

```python
service = PluginService.from_manifest_paths(
    ["plugins"],
    include_unstable=False,
    fail_fast=True,
)
```

Recommended entry point for manifest-based hosts.

Parameters:

- `search_paths` — one or more directories recursively searched for `manifest.json`;
- `include_unstable` — include plugins whose maturity is not `stable`;
- `fail_fast` — raise on malformed manifest/source discovery instead of logging and skipping it;
- `tracer` — optional runtime tracer integration.

This constructor uses strict versioned discovery. Every discovered manifest plugin must declare an explicit version.

#### `service.get(plugin_id)`

Returns a managed plugin instance.

```python
plugin = service.get("example:hello")
```

A reference may include a concrete version:

```python
plugin = service.get("example:hello@1.0.0")
```

#### `service.shutdown(plugin_id)`

Shuts down one managed plugin and removes the runtime-owned managed instance.

#### `service.close()`

Best-effort shutdown of service-owned managed runtime state.

Hosts should call this during application shutdown.

#### `service.execution(...)`

Creates an execution-scoped `ExecutionContext` using an explicit policy.

#### `service.workspace(...)`

Creates an execution context bound to a persistent workspace ID.

### Policies and limits

`ManagedPolicy`, `EphemeralPolicy`, `SandboxLimits`, profile helpers, and `WorkspaceOptions` are host-facing configuration contracts.

For security semantics, read [SECURITY.md](../../SECURITY.md) and the [Capabilities contract](../../CAPABILITIES.md).

## Plugin-author API: `invariant.spec`

Use this namespace from plugin implementations and plugin-side tooling.

```python
from invariant.spec import (
    BasePlugin,
    DependencyScope,
    DependencySpec,
    InvalidVersion,
    PluginContext,
    PluginMeta,
    Version,
    canon_version_key,
)
```

### `BasePlugin`

Base lifecycle contract for concrete plugins.

Required methods:

- `initialize(**deps)`
- `shutdown()`

### `PluginMeta`

Immutable plugin metadata including ID, version, dependencies, categories, tags, author, maturity, repository metadata, and priority.

### `DependencySpec`

Declarative dependency contract containing:

- `id`
- `param`
- `scope`
- `optional`

### `DependencyScope`

Supported values:

- `SINGLETON`
- `EXECUTION`
- `PLUGIN`
- `TRANSIENT`

### `PluginContext`

Protocol for runtime-provided plugin context information such as runtime ID, configuration mapping, and event emission.

### `Version`

Invariant-owned concrete version parser and comparator.

```python
from invariant.spec import Version

assert Version("1.0rc1") < Version("1.0")
```

### `canon_version_key(...)`

Returns the normalized registry key for a concrete version.

```python
from invariant.spec import canon_version_key

assert canon_version_key("1.2") == "1.2.0"
```

## Implementation-level modules

The following are not part of the explicit 0.1 compatibility facade:

```text
invariant.catalog
invariant.load
invariant.exec
invariant.ops
```

Unexported objects inside `invariant.api` and `invariant.spec` are also implementation-level unless documented otherwise.

Advanced users can inspect these modules, but code that imports them directly accepts a higher compatibility risk during the 0.x line.

## Typing

Invariant ships a `py.typed` marker and uses inline Python type annotations. Type checkers can consume the installed package directly.

## Compatibility policy for 0.x

The public facades above are the surfaces Invariant intentionally treats as supported API.

During the 0.x line:

- internal modules may evolve more quickly;
- public facade changes should be intentional and reviewed as compatibility changes;
- new public exports should not be added casually;
- removal or semantic changes to existing public exports should be called out explicitly in release notes.

This is an alpha compatibility contract, not a 1.0 stability guarantee.

## See also

- [Getting started](../getting-started.md)
- [Plugin authoring](../guides/plugin-authoring.md)
- [Manifest v1](manifest-v1.md)
