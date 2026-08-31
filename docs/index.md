# Invariant Documentation

Invariant is a product-agnostic plugin runtime. These docs are organized around the job you are trying to do, not around the repository layout.

## Choose your path

### I want to write a plugin

Start with **[Getting started](getting-started.md)**. You will create a plugin, describe it with a manifest, load it through the public host API, and see the complete lifecycle in one small example.

Then read:

- [Plugin authoring guide](guides/plugin-authoring.md)
- [Manifest v1 reference](reference/manifest-v1.md)
- [Capabilities contract](../CAPABILITIES.md)

### I want to embed Invariant in an application

Start with the **[Public API reference](reference/public-api.md)** and the **[Runtime model](concepts/runtime-model.md)**.

The recommended manifest-first host entry point is:

```python
from invariant.api import PluginService

service = PluginService.from_manifest_paths(["plugins"])
```

### I want to understand or modify the runtime

Read:

- [Runtime model](concepts/runtime-model.md)
- [Core architecture](core.md)
- [Security model](../SECURITY.md)
- [Documentation standard](STYLE.md)

## Documentation map

### Learn

- [Getting started](getting-started.md) — first working plugin, end to end
- [Runtime model](concepts/runtime-model.md) — the small set of concepts that explain how Invariant works

### Guides

- [Plugin authoring](guides/plugin-authoring.md) — lifecycle, metadata, dependencies, versioning, and design guidance

### Reference

- [Public API](reference/public-api.md) — supported import surface for the 0.1 line
- [Manifest v1](reference/manifest-v1.md) — manifest fields, types, defaults, and isolation metadata
- [Capabilities](../CAPABILITIES.md) — capability IDs and security semantics

### Architecture and trust

- [Core architecture](core.md) — implementation layers and internal boundaries
- [Security](../SECURITY.md) — trust model, isolation limits, and vulnerability reporting

## Documentation promise

The docs follow four rules:

1. **Task first.** The shortest path to a working result comes before background theory.
2. **Progressive disclosure.** Basic plugin authoring does not require understanding every runtime subsystem.
3. **Public imports in examples.** User-facing examples do not depend on implementation-level modules unless a page is explicitly about advanced internals.
4. **Executable truth.** Repository examples and documentation links are covered by regression tests so documentation drift is caught with code changes.
