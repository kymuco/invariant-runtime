# Runtime model

Invariant is easier to understand if you keep five concepts separate: **plugin**, **manifest**, **registry**, **service**, and **capability**.

## 1. Plugin

A plugin is a Python object with a lifecycle.

```python
from invariant.spec import BasePlugin


class ExamplePlugin(BasePlugin):
    def initialize(self, **deps) -> None:
        ...

    def shutdown(self) -> None:
        ...
```

The plugin owns domain behavior. The runtime owns activation, dependency wiring, and shutdown.

## 2. Manifest

A manifest describes a plugin without requiring the host to import its code during ordinary discovery.

At minimum, a strict manifest-based plugin declares:

```json
{
  "manifest_version": 1,
  "id": "example:plugin",
  "version": "1.0.0",
  "source": {
    "code_path": "plugin.py",
    "class_name": "ExamplePlugin"
  }
}
```

The manifest is metadata and runtime configuration. It is not executable policy.

See [Manifest v1](../reference/manifest-v1.md).

## 3. Registry

Internally, discovered descriptors enter a versioned registry. The registry answers questions such as:

- which versions of a plugin exist;
- which concrete version matches `example:plugin`;
- whether a pinned version such as `example:plugin@1.2.0` exists.

The registry is an implementation layer in Invariant 0.1. Applications normally reach it through `PluginService` rather than importing registry machinery directly.

## 4. Service

`PluginService` is the supported host-facing facade.

For manifest plugins, the recommended constructor is:

```python
from invariant.api import PluginService

service = PluginService.from_manifest_paths(["plugins"])
```

A managed plugin can then be requested by ID:

```python
plugin = service.get("example:plugin")
```

The service coordinates the runtime layers behind that call.

## 5. Capability

A capability is a runtime-owned handle to a host resource.

Examples include filesystem access, configuration, HTTP, KV storage, workspace access, and runtime inspection.

A capability is **not** the same thing as permission to execute arbitrary host code. Capabilities define an API and policy surface. They are one part of the trust model, not a complete sandbox.

See [Capabilities](../../CAPABILITIES.md) and [Security](../../SECURITY.md).

## Lifecycle

A normal managed activation looks like this:

```text
PluginService.get(plugin_id)
        │
        ▼
resolve versioned descriptor
        │
        ▼
resolve declared dependencies
        │
        ▼
construct plugin instance
        │
        ▼
initialize(resolved_dependencies)
        │
        ▼
active plugin
```

If activation cannot complete, the runtime is responsible for containment and rollback of runtime-owned lifecycle state.

On shutdown:

```text
service.close()
     │
     ▼
shutdown active managed plugins
     │
     ▼
release runtime-owned state
```

## Managed, execution, and workspace scopes

Invariant supports multiple lifetime shapes.

### Managed

`service.get(...)` provides the managed singleton path. Use it when a plugin should be owned by the service for the host lifetime.

### Execution scoped

`service.execution(...)` creates an execution context with a supplied policy and isolated runtime state for that execution.

### Workspace scoped

`service.workspace(...)` creates an execution context bound to a persistent workspace root.

These are runtime ownership models, not business-workflow concepts.

## Version selection

Manifest-first hosts run in strict mode: plugins declare explicit versions.

A reference can be concrete:

```text
example:plugin@1.2.0
```

or select the highest registered non-local version:

```text
example:plugin
example:plugin@latest
```

Invariant owns concrete version parsing and ordering. See the [public API reference](../reference/public-api.md) for the supported `Version` contract.

## Trust boundary

The most important boundary is this:

```text
policy-governed capability ≠ hostile-code sandbox
```

In-process plugins are trusted. Isolated plugin processes provide dependency and failure separation, but stronger external isolation is required for hostile or marketplace code.

Read [Security](../../SECURITY.md) before designing an untrusted-plugin system.
