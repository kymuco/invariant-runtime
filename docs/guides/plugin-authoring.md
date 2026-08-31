# Plugin authoring

Use this guide after [Getting started](../getting-started.md) when your plugin needs to grow beyond the minimal example.

The guiding rule is simple: keep plugin behavior ordinary Python, and let Invariant own lifecycle, dependency wiring, version selection, and policy-governed host access.

## Use the plugin-author API

Plugin code should import supported contracts from `invariant.spec`.

Start with only what the plugin needs:

```python
from invariant.spec import BasePlugin
```

Add contracts such as `DependencySpec`, `DependencyScope`, `PluginContext`, `PluginMeta`, or `Version` only when the code actually uses them.

Avoid coupling ordinary plugin code to `invariant.load`, `invariant.catalog`, `invariant.exec`, or `invariant.ops`. Those remain implementation-level surfaces in the 0.1 line.

## Keep construction lightweight

A concrete plugin implements two lifecycle methods:

```python
from invariant.spec import BasePlugin


class CachePlugin(BasePlugin):
    def initialize(self, **deps) -> None:
        self._ready = True

    def shutdown(self) -> None:
        self._ready = False
```

### `initialize(...)`

Use `initialize(...)` to accept resolved dependencies and prepare runtime state.

Do not depend on constructor-time access to runtime dependencies. The runtime may instantiate the class before dependency wiring is complete.

### `shutdown()`

Release resources owned by the plugin. Make cleanup predictable and safe to call during runtime shutdown.

A plugin should not rely on process exit as its only cleanup strategy.

## Put domain behavior on ordinary methods

Invariant does not prescribe the business API of a plugin.

```python
class GreeterPlugin(BasePlugin):
    def initialize(self, **deps) -> None:
        pass

    def shutdown(self) -> None:
        pass

    def greet(self, name: str) -> str:
        return f"Hello, {name}!"
```

The runtime governs the plugin around these methods; the methods themselves remain ordinary Python.

## Give the plugin a durable identity

A useful ID convention is:

```text
<owner-or-domain>:<plugin-name>
```

Examples:

```text
acme:storage
acme:search
example:hello
```

Plugin IDs are canonicalized case-insensitively and should be treated as durable identities. Changing the ID creates a different plugin identity; use versions to evolve an existing one.

For the recommended manifest-first host path, declare an explicit concrete version:

```json
{
  "manifest_version": 1,
  "id": "acme:greeter",
  "version": "1.0.0",
  "source": {
    "code_path": "plugin.py",
    "class_name": "GreeterPlugin"
  }
}
```

Invariant compares concrete versions deterministically. If tooling needs the same semantics:

```python
from invariant.spec import Version

assert Version("2.0rc1") < Version("2.0")
```

See [Manifest v1](../reference/manifest-v1.md) for accepted metadata and version-related behavior.

## Declare Invariant dependencies explicitly

Use the manifest `dependencies` list for dependencies on another plugin or a capability:

```json
{
  "dependencies": [
    {
      "id": "acme:storage@1.0.0",
      "param": "storage",
      "scope": "EXECUTION",
      "optional": false
    }
  ]
}
```

`param` is the keyword name injected into `initialize(...)`:

```python
class SearchPlugin(BasePlugin):
    def initialize(self, *, storage, **deps) -> None:
        self.storage = storage
```

Available dependency scopes are `SINGLETON`, `EXECUTION`, `PLUGIN`, and `TRANSIENT`. The default is `EXECUTION`.

Keep plugin dependencies explicit rather than discovering collaborators through global state.

## Keep Python dependencies separate

Two manifest fields solve different problems:

```text
dependencies  → other Invariant plugins or capabilities
deps          → third-party Python packages for an isolated plugin environment
```

For a plugin with no isolated Python environment, omit `deps`.

When a plugin needs third-party packages outside the host environment, use the [`deps` section of Manifest v1](../reference/manifest-v1.md#python-environment-dependencies-deps).

Do not treat process isolation as a hostile-code sandbox. See [Security](../../SECURITY.md).

## Use capabilities for governed host resources

A capability is a runtime-controlled handle to a host resource such as filesystem, HTTP, configuration, KV storage, or workspace access.

If a resource is meant to be governed by host policy, access it through the capability surface rather than bypassing that surface through unrelated host state.

Read [Capabilities](../../CAPABILITIES.md) for the built-in capability contract and security semantics.

## Use maturity to control discovery

Manifest `maturity` can be:

- `experimental`
- `beta`
- `stable`
- `deprecated`

The default is `stable`.

`PluginService.from_manifest_paths(...)` excludes non-stable plugins unless the host explicitly passes `include_unstable=True`.

## Design for failure containment

A plugin can fail during construction, initialization, ordinary method calls, or shutdown. Prefer designs that keep failure boundaries visible:

- acquire resources during initialization rather than module import;
- keep external side effects explicit;
- make shutdown idempotent where practical;
- avoid hidden global mutable state;
- raise meaningful exceptions instead of silently corrupting state.

Invariant can govern runtime-owned lifecycle. It cannot automatically make arbitrary external side effects transactional.

## Production checklist

Before calling a plugin production-ready, check that:

- the manifest declares a stable ID and explicit version;
- plugin code uses the supported `invariant.spec` surface;
- initialization and shutdown are deterministic;
- dependencies are declared rather than discovered implicitly;
- governed host resources are reached through capabilities;
- Python isolation requirements are explicit;
- trust assumptions match [SECURITY.md](../../SECURITY.md);
- the plugin has tests independent of the host product.

## Reference

- [Manifest v1](../reference/manifest-v1.md)
- [Public API](../reference/public-api.md)
- [Runtime model](../concepts/runtime-model.md)
- [Capabilities](../../CAPABILITIES.md)
