# Plugin authoring

This guide covers the contracts a plugin author is expected to depend on in Invariant 0.1.

If you have not run a plugin yet, start with [Getting started](../getting-started.md).

## Import from `invariant.spec`

Plugin code should prefer the supported plugin-author facade:

```python
from invariant.spec import (
    BasePlugin,
    DependencyScope,
    DependencySpec,
    PluginContext,
    PluginMeta,
    Version,
)
```

Avoid building plugin code against `invariant.load`, `invariant.catalog`, `invariant.exec`, or `invariant.ops`. Those are implementation-level surfaces in the 0.1 line.

## Implement a small lifecycle

Every concrete `BasePlugin` implements two lifecycle methods:

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

Keep construction lightweight. The runtime may instantiate the class before dependencies are wired; initialization is the lifecycle point for becoming usable.

### `shutdown()`

Release resources owned by the plugin. Make shutdown safe to call as part of runtime cleanup.

A plugin should not rely on process exit as its only cleanup strategy.

## Put domain behavior on ordinary methods

Invariant does not prescribe your business API.

```python
class GreeterPlugin(BasePlugin):
    def initialize(self, **deps) -> None:
        pass

    def shutdown(self) -> None:
        pass

    def greet(self, name: str) -> str:
        return f"Hello, {name}!"
```

The runtime governs lifecycle and dependencies; your plugin defines the behavior meaningful to its host.

## Give every manifest plugin an explicit version

The recommended manifest-first host path runs in strict mode. Declare a concrete version:

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

Invariant normalizes and compares concrete versions deterministically. `1`, `1.0`, and `1.0.0` compare as the same release, while registry keys are normalized to at least three release components.

Use `Version` when plugin-side tooling needs the same concrete-version semantics:

```python
from invariant.spec import Version

assert Version("2.0rc1") < Version("2.0")
```

## Declare plugin dependencies explicitly

Plugin dependencies belong in the manifest `dependencies` list.

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

The fields mean:

- `id` — plugin or capability reference;
- `param` — keyword name used when the dependency is injected into `initialize(...)`;
- `scope` — dependency lifetime;
- `optional` — whether absence may resolve to `None` instead of failing activation.

Then receive the dependency by its declared parameter name:

```python
class SearchPlugin(BasePlugin):
    def initialize(self, *, storage, **deps) -> None:
        self.storage = storage
```

Supported dependency scopes are `SINGLETON`, `EXECUTION`, `PLUGIN`, and `TRANSIENT`.

## Treat capabilities as host resources

A capability is a runtime-controlled handle to something outside the plugin, such as filesystem, HTTP, configuration, or workspace access.

Do not bypass a capability by reaching directly into host state if you expect policy to govern that resource.

Read [Capabilities](../../CAPABILITIES.md) for capability IDs and policy semantics.

## Keep Python package isolation separate from plugin dependencies

Two manifest fields solve different problems:

```text
dependencies  → dependencies on other Invariant plugins/capabilities
deps          → Python environment dependencies for isolated execution
```

Do not mix them conceptually.

For a plugin that needs no third-party Python packages, omit `deps` entirely.

For isolated Python dependencies, see the [`deps` section of Manifest v1](../reference/manifest-v1.md#python-environment-dependencies-deps).

## Use maturity intentionally

`maturity` communicates whether discovery should treat a plugin as stable by default.

Allowed values:

- `experimental`
- `beta`
- `stable`
- `deprecated`

The default is `stable`.

`PluginService.from_manifest_paths(...)` excludes non-stable plugins unless the host explicitly passes `include_unstable=True`.

## Keep IDs stable

Plugin IDs are case-insensitive after canonicalization and are intended to be durable identities.

A useful convention is:

```text
<owner-or-domain>:<plugin-name>
```

Examples:

```text
acme:storage
acme:search
example:hello
```

Changing a plugin ID creates a different identity. Use versions to evolve an existing plugin contract.

## Design for failure containment

A plugin can fail during construction, initialization, method calls, or shutdown. Prefer designs that make cleanup predictable:

- acquire resources during initialization rather than module import;
- keep external side effects explicit;
- make shutdown idempotent where practical;
- avoid hidden global mutable state;
- surface meaningful exceptions rather than silently corrupting state.

Invariant can govern runtime lifecycle, but it cannot make arbitrary plugin side effects transactional automatically.

## Before calling a plugin production-ready

Check that:

- the manifest has an explicit version;
- public plugin code imports from `invariant.spec` rather than internal layers;
- initialization and shutdown are deterministic;
- dependencies are declared rather than discovered implicitly;
- capabilities are used for governed host resources;
- security assumptions match [SECURITY.md](../../SECURITY.md);
- the plugin has tests independent of the host product.

## Reference

- [Manifest v1](../reference/manifest-v1.md)
- [Public API](../reference/public-api.md)
- [Runtime model](../concepts/runtime-model.md)
- [Capabilities](../../CAPABILITIES.md)
