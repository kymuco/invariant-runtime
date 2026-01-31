# Project Invariant – Capabilities Contract

## What is a Capability
A capability is a platform-owned, policy-controlled handle to a resource.
Plugins do NOT register capabilities. Plugins only request them via dependencies.

Capability IDs are namespaced:
- `invariant:capability.<name>`

Capability references MAY be version-pinned:
- `invariant:capability.fs_sandbox@0.2.0`

## Security Model (Important)
- In-process plugins are **trusted code**.
- Capabilities are an **API + policy surface**, not a sandbox security boundary.
- For untrusted/marketplace plugins, isolation must be provided by a separate runtime layer (process/container + resource limits).

## Availability by Policy / InstanceMode
- `MANAGED` runtime: **allowlist only**
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
- descriptors list
- instances list
- dependency graph
- env info (sandbox/workspace summary)

### Optional capabilities (if enabled by policy)
- `invariant:capability.http` (safe allowlist HTTP client)
- `invariant:capability.kv`  (simple KV store, persisted in workspace/sandbox)

## Observability: emitted events
Runtime emits structured events for capability operations (best-effort, non-failing):
- `cap.fs.read_bytes`, `cap.fs.read_text`
- `cap.fs.write_bytes`, `cap.fs.write_text`
- `cap.fs.list_dir`, `cap.fs.delete_file`, `cap.fs.delete_tree`
- `cap.http.get_*`, `cap.http.post_json`
- `cap.kv.get`, `cap.kv.set`, `cap.kv.delete`, `cap.kv.list_keys`

Each event includes:
- `cap_id`
- operation-specific fields (e.g., `rel`, `bytes`, `dt_ms`, `ok`, `error_type`, `error`)

## Versioning rules
- Capability versions are normalized and compared as pinned strings in strict mode.
- Contract is forward-compatible only when version changes are explicit.
