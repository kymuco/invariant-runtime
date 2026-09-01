---
hide:
  - toc
---

<div class="iv-hero" markdown>

<span class="iv-eyebrow">INVARIANT RUNTIME · 0.1 ALPHA</span>

# Runtime boundaries you can reason about.

Invariant is a small, policy-aware plugin runtime for Python: versioned discovery, explicit dependencies, governed host capabilities, and deterministic lifecycle without product behavior in the core.

<div class="iv-actions" markdown>
[Build your first plugin](getting-started.md){ .iv-button .iv-button--primary }
[Understand the runtime](concepts/runtime-model.md){ .iv-button }
</div>

<div class="iv-proof">
<span>Zero runtime dependencies</span>
<span>Strict manifests</span>
<span>Scoped capabilities</span>
<span>Transactional lifecycle</span>
</div>

</div>

## Start with the job in front of you

<div class="iv-path-grid" markdown>

<div class="iv-path-card" markdown>
<span class="iv-card-label">PLUGIN AUTHOR</span>

### Build a plugin

Write ordinary Python behavior, declare it with a small manifest, and let Invariant own discovery, version selection, dependency wiring, and lifecycle.

[Getting started →](getting-started.md)
</div>

<div class="iv-path-card" markdown>
<span class="iv-card-label">APPLICATION HOST</span>

### Embed the runtime

Use the narrow `invariant.api` facade to load manifests, request managed plugins, and create scoped execution or workspace contexts.

[Public API →](reference/public-api.md)
</div>

<div class="iv-path-card" markdown>
<span class="iv-card-label">RUNTIME CONTRIBUTOR</span>

### Work on the core

Understand the internal boundary between discovery, registry, resolution, execution, capabilities, and operational infrastructure.

[Core architecture →](core.md)
</div>

</div>

## One public path, not a pile of internals

<div class="iv-split" markdown>

<div class="iv-split-copy" markdown>

### Host-facing API

A manifest-first host should not need to construct loaders, registries, or resolvers itself. `PluginService` keeps those layers behind one supported facade.

The root package stays deliberately small; host code uses `invariant.api`, while plugin implementations use `invariant.spec`.

[Read the compatibility contract](reference/public-api.md)

</div>

```python
from invariant.api import PluginService

service = PluginService.from_manifest_paths(["plugins"])
try:
    plugin = service.get("example:hello")
finally:
    service.close()
```

</div>

## Explicit boundaries at every layer

<div class="iv-boundary-grid" markdown>

<div class="iv-boundary" markdown>

### Identity & version

Plugins have durable IDs and concrete versions. Selection is deterministic rather than inferred from import order or ambient state.

</div>

<div class="iv-boundary" markdown>

### Dependencies & lifecycle

Dependencies are declared. Activation, rollback, managed ownership, and shutdown belong to the runtime rather than to product glue.

</div>

<div class="iv-boundary" markdown>

### Capabilities & policy

Host resources are exposed through explicit capability interfaces so applications can govern access at a stable API boundary.

</div>

</div>

<div class="iv-security" markdown>

<span class="iv-card-label">SECURITY BOUNDARY</span>

### Policy control is not hostile-code isolation.

Capabilities govern access through Invariant APIs, but in-process plugins are trusted Python code. Marketplace or hostile plugins require stronger external isolation for filesystem, network, processes, resources, and secrets.

[Read the security model →](security.md)

</div>

## Documentation map

| If you need to… | Start here |
| --- | --- |
| Run a first plugin end to end | [Getting started](getting-started.md) |
| Learn the four core concepts | [Runtime model](concepts/runtime-model.md) |
| Grow a production plugin | [Plugin authoring](guides/plugin-authoring.md) |
| Embed Invariant in an application | [Public API](reference/public-api.md) |
| Define exact plugin metadata | [Manifest v1](reference/manifest-v1.md) |
| Work with governed host resources | [Capabilities](reference/capabilities.md) |
| Understand trust and isolation | [Security](security.md) |
| Modify the runtime itself | [Core architecture](core.md) |

## Documentation promise

The site follows four rules: **task first**, **progressive disclosure**, **public imports in user-facing examples**, and **executable truth**. Repository examples, links, rendered-site configuration, and the first-plugin tutorial are covered by regression tests so documentation drift fails with code changes.
