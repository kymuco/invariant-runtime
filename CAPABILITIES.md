# Invariant Runtime — Capabilities Contract

## What is a capability

A capability is a runtime-owned, policy-controlled handle to a resource.
Plugins do **not** register capabilities. Plugins request them through runtime dependency wiring.

Capability IDs are namespaced:

- `invariant:capability.<name>`

Capability references may be version-pinned:

- `invariant:capability.fs_sandbox@0.3.0`

A declared capability reference is a **requirement**, not authority. The host policy still decides whether a particular execution receives the capability.

## Plugin-author surface

Plugin code and plugin-side tooling use `invariant.spec` rather than importing capability implementations from `invariant.exec`.

Public versioned references:

```python
from invariant.spec import FS_SANDBOX_CAPABILITY

assert FS_SANDBOX_CAPABILITY.id == "invariant:capability.fs_sandbox"
assert FS_SANDBOX_CAPABILITY.version == "0.3.0"
assert FS_SANDBOX_CAPABILITY.ref == "invariant:capability.fs_sandbox@0.3.0"
```

The built-in plugin-facing references are:

| Public reference | Runtime reference |
| --- | --- |
| `FS_SANDBOX_CAPABILITY` | `invariant:capability.fs_sandbox@0.3.0` |
| `WORKSPACE_CAPABILITY` | `invariant:capability.workspace@0.3.0` |
| `HTTP_CAPABILITY` | `invariant:capability.http@0.1.0` |
| `KEY_VALUE_CAPABILITY` | `invariant:capability.kv@0.1.0` |
| `PROCESS_CAPABILITY` | `invariant:capability.process@0.2.0` |
| `CONFIG_SOURCE_CAPABILITY` | `invariant:capability.config_source@0.1.0` |
| `INSPECTOR_CAPABILITY` | `invariant:capability.inspector@0.1.0` |

`BUILTIN_CAPABILITIES` contains these public contracts for tooling that needs to enumerate them. Runtime-only infrastructure such as the internal audit sink is deliberately not part of this plugin-author list.

### Type against behavior, not implementations

`invariant.spec` also exposes structural Protocols:

- `FilesystemCapability`
- `HttpCapability`
- `KeyValueCapability`
- `ProcessCapability`
- `ConfigSourceCapability`
- `RuntimeInspectorCapability`

For example:

```python
from invariant.spec import BasePlugin, FilesystemCapability


class NotesStoragePlugin(BasePlugin):
    def initialize(self, *, fs: FilesystemCapability, **deps) -> None:
        self._fs = fs
```

The filesystem Protocol intentionally exposes operations such as `read_text`, `write_text`, `mkdir`, `list_dir`, and permission/limit queries. It does **not** expose implementation layout such as an underlying root path or the concrete resolver used internally.

A Protocol annotation does not perform capability lookup and does not grant access. Runtime dependency resolution still supplies the actual handle declared by the manifest.

## Operation errors versus acquisition errors

There are two different failure boundaries.

**Host acquisition / authority failures** are exposed from `invariant.api`, for example `CapabilityUnavailableError`. A host may catch this when a graph requires a capability that current policy does not grant.

**Plugin operation failures** happen after a handle has already been granted. Portable plugin code may catch broad categories from `invariant.spec`:

- `CapabilityOperationError`
- `FilesystemCapabilityError`
- `HttpCapabilityError`
- `KeyValueCapabilityError`
- `ProcessCapabilityError`

Concrete runtime failures such as a denied filesystem write, blocked HTTP host, or corrupt KV store remain implementation-specific subclasses of those broad contracts.

## Security model

- In-process plugins are **trusted code**.
- Capabilities are an **API + policy surface**, not a complete sandbox security boundary.
- Isolated plugin workers are process boundaries, but not sufficient hostile-code sandboxes by themselves.
- Untrusted/marketplace plugins require a stronger external isolation layer with explicit filesystem, network, process, resource, secret, and package-installation controls.

## Availability by policy / instance mode

- `MANAGED` runtime: restricted allowlist for safe capabilities such as inspection/configuration.
- Execution-scoped runtimes: policy-gated, configured by `SandboxLimits` and `WorkspaceOptions`.

## Built-in capabilities

### invariant:capability.fs_sandbox @ 0.3.0

Execution-scoped filesystem access rooted inside Invariant's execution sandbox.

Properties:

- traversal blocked (`..`, absolute paths)
- byte limits on read/write
- policy flags: write/delete
- atomic writes supported
- governed directory creation through `mkdir(...)`

### invariant:capability.workspace @ 0.3.0

Persistent workspace-scoped filesystem access.

Only available when an execution is created with workspace configuration, normally through `PluginService.workspace(...)`.

### invariant:capability.http @ 0.1.0

Governed outbound HTTP(S) access.

Properties include:

- host allowlist / deny-all behavior
- response byte limits
- timeouts
- redirect policy
- private-address protections

Network access is opt-in under host policy.

### invariant:capability.kv @ 0.1.0

Simple JSON-backed key-value storage over an execution/workspace filesystem capability.

The capability is only available when policy permits the storage path it requires.

### invariant:capability.process @ 0.2.0

Workspace-bound synchronous and durable background process execution.

Properties:

- `run(...)` preserves the bounded synchronous process contract
- `start(key, ...)` creates an idempotent durable background process keyed by opaque technical identity
- `status(handle)`, `terminate(handle)`, and `read_output(handle)` operate on the returned opaque handle
- replay with the same key and request reuses the existing durable process rather than starting a second child
- reuse of the same key for a different request is rejected
- relative cwd is resolved and confined inside the hidden workspace root
- synchronous timeout and all exposed stdout/stderr are bounded by host policy
- command arguments, process keys, and environment values are not part of the capability audit payload
- the capability does not expose the workspace root or resolver to plugin code
- uncertain in-flight state is reported as `unknown`; the capability never auto-restarts it

The process key is an idempotency/reconciliation primitive, not permission or retry authority. Product-level admission remains upstream of Invariant.

### invariant:capability.config_source @ 0.1.0

Read-only configuration source for plugins:

- reads from policy-provided config map
- optionally reads from environment when the host explicitly enables prefixed environment access

### invariant:capability.inspector @ 0.1.0

Safe read-only runtime snapshot:

- descriptor list
- instance list
- dependency graph
- selected environment information

## Observability: emitted events

Runtime emits structured events for capability operations on a best-effort, non-failing basis, including:

- `cap.fs.read_bytes`, `cap.fs.write_bytes`, `cap.fs.mkdir`
- `cap.fs.list_dir`, `cap.fs.delete_file`, `cap.fs.delete_tree`
- `cap.http.get_*`, `cap.http.post_json`
- `cap.process.run`, `cap.process.start`, `cap.process.status`, `cap.process.terminate`
- `cap.kv.get`, `cap.kv.set`, `cap.kv.delete`, `cap.kv.list_keys`

Events contain the capability ID and operation metadata. Sensitive values should not be treated as part of the audit contract.

## Versioning rules

- Public built-in IDs and versions live in `invariant.spec` and feed the runtime catalog.
- Capability versions use Invariant's concrete-version normalization and ordering.
- Strict version matching applies to version-pinned capability references.
- Contract compatibility must be made explicit through version changes; compatibility must not be inferred from an unversioned capability reference.
