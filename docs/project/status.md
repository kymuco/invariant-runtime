# Project status

This page separates what Invariant currently supports from what its alpha status does **not** promise. It is a snapshot of the **0.1 line**, not a list of future features presented as complete.

## Supported direction

Invariant is an embeddable, product-independent plugin runtime for Python 3.11+. It uses the standard library at runtime and declares no mandatory third-party runtime dependencies.

Its public interfaces are deliberately limited:

- Hosts use `invariant.api`, especially `PluginService` and policy helpers.
- Plugin authors use `invariant.spec` for `BasePlugin`, versioned capability references, Protocols, metadata and dependency declarations.
- `invariant.__version__` reports the installed package distribution version.
- Manifests and filesystem discovery are supported source paths, with explicit concrete versions.
- Managed and execution-scoped ownership, cleanup, runtime-owned rollback and governed capabilities are implemented within the published contracts.

Read [Public API](../reference/public-api.md), [Manifest v1](../reference/manifest-v1.md), and [Capabilities](../reference/capabilities.md) for exact names, defaults and errors.

## Compatibility status

**The published-source baseline is 0.1.0 alpha; this branch stages an unpublished 0.1.1a1 candidate.** Alpha means public API compatibility changes remain possible before 1.0, although deliberate changes must be documented and tested.

The project can stage experimental version candidates in separate branches; an open PR is not a published release. Prefer the package's actual `__version__` when reporting problems.

`invariant.load`, `invariant.catalog`, `invariant.exec`, and `invariant.ops` are implementation-level modules and not part of the explicit public 0.1 facade.

## Known limits — read before deploying

| Area | Current boundary |
| :-- | :-- |
| Untrusted code | In-process plugins are trusted. Isolated workers are not complete sandboxes for hostile or marketplace plugins. |
| Rollback | Reverses runtime-owned activation state where supported; it cannot undo arbitrary external side effects. |
| Shutdown | Cleanup can be best-effort or deferred until in-flight work drains. A logical close event does not prove an uncooperative plugin stopped. |
| Discovery | `ManifestSource` and `FilesystemSource` are implemented; entrypoint, ZIP and remote source loaders are not production-ready. |
| Telemetry | Detailed supervisor, tracing and metrics APIs remain internal; they are not a durable public operational schema. |
| Packaging | The runtime is dependency-free; isolated plugins, test and documentation environments may add dependencies of their own. |

For threat assumptions and reporting, see [Security](../security.md). For runtime-internal design, see [Core architecture](../core.md).

## What comes next

Development priorities are selected by concrete consumer-independent failures rather than speculative framework features:

1. Keep public examples, versioned capability documentation and installed-wheel tests aligned with actual implementation.
2. Preserve clean cross-platform core CI and validate any change to lifecycle/teardown against concurrency cases.
3. Improve public documentation for real plugin authors, host integrations and safe boundary use.
4. Consider narrowly specified operational diagnostics only after a concrete need is supported by failing tests or reproducible field evidence.

No general hot-swap contract, hostile-plugin sandbox or experimental source backend is promised by this roadmap.
