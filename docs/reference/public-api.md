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

## Host-facing error contract

Expected runtime failures that a host may need for control flow are exported from `invariant.api` and from the dedicated `invariant.api.errors` module.

| Error | Meaning |
| --- | --- |
| `PluginError` | Base class for public plugin-system failures. |
| `PluginNotFoundError` | Requested plugin ID or concrete version is unavailable. |
| `PluginLoadError` | A plugin manifest or implementation cannot be loaded under the runtime contract. |
| `DependencyCycleError` | The requested dependency graph contains a cycle. |
| `AmbiguousPluginReference` | An unpinned reference cannot select one concrete plugin build. |
| `CapabilityError` | Base class for capability lookup/version failures. |
| `CapabilityNotFound` | A requested capability is not registered in the current provider. |
| `CapabilityVersionMismatch` | The requested capability version does not match the provided contract. |
| `CapabilityUnavailableError` | The capability exists conceptually but current runtime policy does not allow it. |

For example, a host can treat policy denial as an expected branch without importing execution internals:

```python
from invariant.api import CapabilityUnavailableError

try:
    plugin = execution.get("example:notes")
except CapabilityUnavailableError:
    # The graph requires authority this execution did not receive.
    ...
```

Likewise, plugin lookup can stay on the public facade:

```python
from invariant.api import PluginNotFoundError

try:
    plugin = service.get("example:optional-feature")
except PluginNotFoundError:
    plugin = None
```

These public names are identity-preserving aliases of the exception classes used by the runtime. The modules that currently define those classes are implementation details; host code should import the public aliases instead.

Not every internal exception is promoted. Errors that represent implementation-only activation states remain internal until a host-facing recovery contract requires them.

## Plugin-author API: `invariant.spec`

Plugin implementations and plugin-side tooling should import from `invariant.spec`.

### Core plugin contracts

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

### Capability authoring contracts

Public capability identities, structural types, and broad operation errors also live in `invariant.spec`.

| Export | Purpose |
| --- | --- |
| `CapabilityRef` | Immutable built-in capability ID + concrete contract version. |
| `BUILTIN_CAPABILITIES` | Enumerated plugin-facing built-in capability references. |
| `FS_SANDBOX_CAPABILITY` | `fs_sandbox@0.2.0` contract. |
| `WORKSPACE_CAPABILITY` | `workspace@0.2.0` contract. |
| `HTTP_CAPABILITY` | `http@0.1.0` contract. |
| `KEY_VALUE_CAPABILITY` | `kv@0.1.0` contract. |
| `CONFIG_SOURCE_CAPABILITY` | `config_source@0.1.0` contract. |
| `INSPECTOR_CAPABILITY` | `inspector@0.1.0` contract. |
| `FilesystemCapability` | Structural filesystem operations available to plugin code. |
| `HttpCapability` | Structural governed HTTP operations. |
| `KeyValueCapability` | Structural key-value operations. |
| `ConfigSourceCapability` | Structural read-only configuration operations. |
| `RuntimeInspectorCapability` | Structural read-only runtime inspection operations. |
| `CapabilityOperationError` | Base error after a capability handle has been granted. |
| `FilesystemCapabilityError` | Broad filesystem operation failure. |
| `HttpCapabilityError` | Broad HTTP operation failure. |
| `KeyValueCapabilityError` | Broad key-value operation failure. |

A capability reference is descriptive, not authoritative:

```python
from invariant.spec import FS_SANDBOX_CAPABILITY

assert FS_SANDBOX_CAPABILITY.ref == "invariant:capability.fs_sandbox@0.2.0"
```

The manifest still declares the requirement as data, and the host policy still decides whether the execution receives it.

Type plugin code against the structural contract rather than the runtime implementation:

```python
from invariant.spec import BasePlugin, FilesystemCapability


class NotesStoragePlugin(BasePlugin):
    def initialize(self, *, fs: FilesystemCapability, **deps) -> None:
        self._fs = fs
```

`FilesystemCapability` intentionally omits concrete implementation layout such as an underlying root path or internal resolver. It describes operations the plugin may rely on, not how the runtime implements them.

If a granted handle later fails an operation, plugin code can catch a broad contract error without importing `invariant.exec`:

```python
from invariant.spec import FilesystemCapabilityError

try:
    fs.write_text("note.txt", text)
except FilesystemCapabilityError:
    ...
```

This is distinct from `invariant.api.CapabilityUnavailableError`, which is a host-facing acquisition/authority failure before the plugin receives the required handle.

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

Advanced integrations may inspect or use those modules, but direct imports accept a higher compatibility risk during the 0.x line. Public aliases and Protocols remain the compatibility surface even when their implementation currently lives in one of these modules.

## Typing

Invariant ships a `py.typed` marker and inline Python annotations. Type checkers can consume the installed package directly.

Capability Protocols are structural: plugins depend on the behavior they need, while the runtime remains free to change the concrete implementation as long as the declared contract remains compatible.

## Compatibility rules for 0.x

- new public exports should be added deliberately;
- removal or semantic changes to an existing public export should be called out in release notes;
- built-in capability ID/version changes are compatibility changes;
- implementation-level modules may change without the same compatibility promise;
- examples and ordinary guides should use the public facades unless they are explicitly documenting internals.

## See also

- [Getting started](../getting-started.md)
- [Plugin authoring](../guides/plugin-authoring.md)
- [Dependencies and capabilities](../guides/dependencies-and-capabilities.md)
- [Manifest v1](manifest-v1.md)
- [Capabilities](capabilities.md)
