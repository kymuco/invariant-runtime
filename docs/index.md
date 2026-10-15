---
hide:
  - toc
---

<div class="iv-hero" markdown>

<span class="iv-eyebrow">OPEN CONTRACTS / PYTHON 3.11+ / APACHE-2.0</span>

# Plugins, by contract.

A lightweight Python runtime for plugins with explicit identities, declared dependencies, policy-governed access, and a lifecycle the host can reason about. No mandatory third-party runtime dependencies. No framework takeover.

<div class="iv-actions">
<a href="getting-started/" class="iv-button iv-button--primary">Build your first plugin →</a>
<a href="concepts/runtime-model/" class="iv-button">Understand the boundaries</a>
</div>

<div class="iv-hero-art">
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/invariant-hero-dark.svg">
  <img src="assets/invariant-hero-light.svg" alt="Invariant lifecycle schematic: discover, resolve, activate, release" width="100%">
</picture>
</div>

<div class="iv-proof">
<span>stdlib-only core</span>
<span>versioned manifests</span>
<span>governed capabilities</span>
<span>owned lifecycle</span>
</div>

</div>

## Start with the work you're doing

<div class="iv-path-grid">

<div class="iv-path-card" markdown>
<span class="iv-card-label">01 / PLUGIN AUTHOR</span>

### Build a plugin

Write a Python class, declare a versioned manifest, and let the runtime handle discovery and activation.

[Build your first plugin →](getting-started.md)
</div>

<div class="iv-path-card" markdown>
<span class="iv-card-label">02 / APPLICATION HOST</span>

### Embed the runtime

Use the supported `PluginService` facade. Keep loaders, registries, and resolver machinery inside the runtime.

[Explore the public API →](reference/public-api.md)
</div>

<div class="iv-path-card" markdown>
<span class="iv-card-label">03 / SYSTEM DESIGNER</span>

### Define boundaries

Choose lifetimes, explicit dependencies, and governed host-resource handles without coupling plugins to your application internals.

[See the runtime model →](concepts/runtime-model.md)
</div>

</div>

## One small public facade

<div class="iv-split" markdown>

<div class="iv-split-copy" markdown>

<span class="iv-card-label">HOST API / `invariant.api`</span>

### The host stays in control

A host declares where to discover manifests and requests a plugin by identity. Invariant manages the machinery behind that call.

Plugin implementations depend on `invariant.spec` rather than internal runtime modules.

[See public API and errors →](reference/public-api.md)

</div>

```python
from invariant.api import PluginService

service = PluginService.from_manifest_paths(["plugins"])
try:
    greeter = service.get("example:hello")
    print(greeter.greet("Invariant"))
finally:
    service.close()
```

</div>

## Four responsibilities. Clear boundaries.

<div class="iv-stage-track">
<div class="iv-stage"><span class="iv-stage-index">01 / DISCOVER</span><strong>Identity</strong><p>Find manifests and select concrete versions.</p></div>
<div class="iv-stage"><span class="iv-stage-index">02 / RESOLVE</span><strong>Dependencies</strong><p>Construct declared plugin and capability relationships.</p></div>
<div class="iv-stage"><span class="iv-stage-index">03 / ACTIVATE</span><strong>Authority</strong><p>Wire policy-permitted handles and establish runtime-owned state.</p></div>
<div class="iv-stage"><span class="iv-stage-index">04 / RELEASE</span><strong>Lifecycle</strong><p>Shut down and roll back runtime-owned state within documented limits.</p></div>
</div>

Read the [runtime model](concepts/runtime-model.md) to see these layers in context.

<div class="iv-security" markdown>

<span class="iv-card-label">BOUNDARY / TRUST MODEL</span>

### Capabilities are not a sandbox.

Policy-governed handles restrict the access made through those handles. They do not prevent trusted in-process Python code from using other APIs. Isolated workers are not a complete hostile-code sandbox, and cleanup cannot reverse arbitrary external effects.

[Read the security guarantees and exclusions →](security.md)

</div>

## Reference, without the maze

| Need | Exact documentation |
| :-- | :-- |
| Minimal setup and expected output | [Getting started](getting-started.md) |
| Plugin lifecycle and manifest authoring | [Plugin authoring](guides/plugin-authoring.md) |
| Plugin versus capability dependencies | [Dependencies and capabilities](guides/dependencies-and-capabilities.md) |
| Names, defaults, and import stability | [Public API](reference/public-api.md) |
| Manifest structure | [Manifest v1](reference/manifest-v1.md) |
| Built-in handles and policy constraints | [Capabilities](reference/capabilities.md) |
| Compatibility, known limits, and roadmap | [Project status](project/status.md) |
| How to contribute safely | [Contributing](project/contributing.md) |

<small class="iv-footnote">Invariant 0.1 is alpha software. Documentation distinguishes the supported public facade from internal implementation details and from capabilities that are not ready.</small>
