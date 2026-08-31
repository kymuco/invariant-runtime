# Invariant Runtime — Capabilities Contract

## What is a capability

A capability is a runtime-owned, policy-controlled handle to a resource.
Plugins do **not** register capabilities. Plugins request them through runtime dependency wiring.

Capability IDs are namespaced:

- `invariant:capability.<name>`

Capability references may be version-pinned:

- `invariant:capability.fs_sandbox@0.2.0`

## Security model

- In-process plugins are **trusted code**.
- Capabilities are an **API + policy surface**, not a complete sandbox security boundary.
- Isolated plugin workers are process boundaries, but not sufficient hostile-code sandboxes by themselves.
- Untrusted/marketplace plugins require a stronger external isolation layer with explicit filesystem, network, process, resource, secret, and package-installation controls.

## Availability by policy / instance mode

- `MANAGED` runtime: allowlist only
  - `invariant:capability.inspector`
- Execution-scoped runtimes: policy-gated, configured by `SandboxLimits` and `WorkspaceOptions`.

## Built-in capabilities

### invariant:capability.fs_sandbox @ 0.2.0

Execution-scoped filesystem access rooted at:

- `.invariant/sandbox/<execution_id>`

Properties:

- traversal blocked (`..`, absolute paths)
- byte limits on read/write
- policy flags: write/delete
- atomic writes supported

### invariant:capability.workspace @ 0.2.0

Persistent filesystem access rooted at:

- `<cwd>/<WorkspaceOptions.root_dirname>/<workspace_id>`

Only available via `PluginService.workspace(...)`.

### invariant:capability.config_source @ 0.1.0

Configuration source for plugins:

- reads from policy-provided config map
- optionally reads from environment (prefixed keys)

### invariant:capability.inspector @ 0.1.0

Safe read-only runtime snapshot:

- descriptor list
- instance list
- dependency graph
- environment summary (sandbox/workspace)

### Optional capabilities (when enabled by policy)

- `invariant:capability.http` — allowlist-oriented HTTP client
- `invariant:capability.kv` — simple KV store persisted in workspace/sandbox

## Observability: emitted events

Runtime emits structured events for capability operations on a best-effort, non-failing basis, including:

- `cap.fs.read_bytes`, `cap.fs.read_text`
- `cap.fs.write_bytes`, `cap.fs.write_text`
- `cap.fs.list_dir`, `cap.fs.delete_file`, `cap.fs.delete_tree`
- `cap.http.get_*`, `cap.http.post_json`
- `cap.kv.get`, `cap.kv.set`, `cap.kv.delete`, `cap.kv.list_keys`

Each event includes:

- `cap_id`
- operation-specific fields such as `rel`, `bytes`, `dt_ms`, `ok`, `error_type`, and `error`

## Versioning rules

- Capability versions use Invariant's concrete-version normalization and ordering.
- Strict mode requires valid explicit versions.
- Contract compatibility should be made explicit through version changes; compatibility must not be inferred from an unversioned capability reference.
