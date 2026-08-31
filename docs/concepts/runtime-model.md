# Runtime model

You only need four ideas to use Invariant well: **plugin**, **manifest**, **service**, and **capability**.

The loader, registry, resolver, and execution runtime still exist, but ordinary application and plugin code should not need to assemble them directly.

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

The plugin owns domain behavior. Invariant owns runtime lifecycle around that behavior.

## 2. Manifest

A manifest describes a plugin before the host needs to activate it.

A minimal strict manifest is:

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

The manifest carries identity, version, source location, and optional runtime metadata. It is declarative data, not executable policy.

See [Manifest v1](../reference/manifest-v1.md).

## 3. Service

`PluginService` is the supported host-facing facade.

For manifest plugins:

```python
from invariant.api import PluginService

service = PluginService.from_manifest_paths(["plugins"])
```

Request a managed plugin by ID:

```python
plugin = service.get("example:plugin")
```

Close the service when the host shuts down:

```python
service.close()
```

The service owns the runtime machinery behind those operations.

## 4. Capability

A capability is a runtime-owned, policy-controlled handle to a host resource.

Examples include filesystem access, configuration, HTTP, KV storage, workspace access, and runtime inspection.

Capabilities let a host govern access through explicit interfaces. They are **not** a complete sandbox for hostile Python code.

See [Capabilities](../reference/capabilities.md) and [Security](../security.md).

## What happens behind the service

You do not need these layers to write a first plugin, but they explain the runtime:

```text
manifest / source
       │
       ▼
   discovery
       │
       ▼
versioned registry
       │
       ▼
dependency resolution
       │
       ▼
plugin construction
       │
       ▼
initialize(resolved_dependencies)
       │
       ▼
active plugin
```

The internal responsibilities stay separate:

- discovery finds plugin descriptions;
- the registry owns concrete version selection;
- the resolver builds dependency relationships;
- the execution runtime owns activation and cleanup;
- `PluginService` presents the supported host-facing facade.

This separation is useful for contributors and advanced integrations, but it is intentionally hidden from the ordinary onboarding path.

## Lifecycle

For a managed plugin, `service.get(...)` activates the plugin on first use and returns the runtime-owned managed instance.

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

If activation fails, Invariant contains and rolls back runtime-owned lifecycle state where the execution model supports it. Arbitrary external side effects performed by plugin code are not automatically transactional.

## Lifetime shapes

Invariant supports three common host-facing shapes.

### Managed

```python
plugin = service.get("example:plugin")
```

Use this when the service should own one managed instance for the host lifetime.

### Execution scoped

```python
context = service.execution(policy=policy)
```

Use an execution context when a unit of work needs its own policy and runtime-owned state.

### Workspace scoped

```python
context = service.workspace("workspace-id")
```

Use a workspace context when execution also needs a persistent workspace root.

These are ownership and lifetime models, not business-workflow concepts.

## Version selection

Manifest-first hosts use strict discovery: every discovered plugin declares an explicit version.

Pin a concrete version when you need it:

```text
example:plugin@1.2.0
```

Or let the registry select the highest registered non-local version:

```text
example:plugin
example:plugin@latest
```

Invariant owns concrete version parsing and ordering. See the [Public API reference](../reference/public-api.md) for the supported `Version` contract.

## Trust boundary

Keep this distinction explicit:

```text
policy-governed capability ≠ hostile-code sandbox
```

In-process plugins are trusted code. Isolated plugin workers provide dependency and failure separation, but hostile or marketplace code requires stronger external isolation.

Read [Security](../security.md) before designing an untrusted-plugin system.
