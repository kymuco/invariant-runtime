# Invariant Runtime

Invariant is a policy-aware, product-agnostic plugin runtime for versioned discovery, dependency resolution, scoped capabilities, and transactional execution.

The runtime is intentionally small at the dependency boundary: **Invariant has zero mandatory third-party runtime dependencies** and runs on the Python standard library.

## What it provides

- versioned plugin descriptors and manifests
- deterministic concrete-version normalization and ordering
- dependency resolution and activation planning
- transactional plugin activation and rollback
- policy-gated capabilities for filesystem, workspace, HTTP, KV, configuration, and inspection
- managed, execution-scoped, and workspace-scoped runtime modes
- isolated plugin worker support
- host, daemon, supervisor, tracing, metrics, and watchdog infrastructure

Invariant is infrastructure, not a product workflow layer. Domain plugins, agents, companions, application orchestration, and marketplace policy belong outside the runtime.

## Install from source

```bash
python -m pip install .
```

For development and tests:

```bash
python -m pip install -e ".[test]"
python -m pytest -q tests/core
```

## Minimal example

```python
from invariant import __version__
from invariant.spec import PluginMeta, Version, canon_version_key

meta = PluginMeta(id="example:hello", version="1.2rc1")

assert Version("1.2rc1") < Version("1.2")
assert canon_version_key("1.2") == "1.2.0"
print(__version__, meta.id, meta.version)
```

## Public import contract

Invariant 0.1 keeps its compatibility surface deliberately narrow.

### Package metadata

```python
from invariant import __version__
```

The root package intentionally exposes only distribution metadata. Runtime classes are not re-exported from the root namespace.

### Host-facing API

Use `invariant.api` for the supported host facade:

```python
from invariant.api import (
    EphemeralPolicy,
    PluginService,
    SandboxLimits,
    WorkspaceOptions,
)
```

This namespace owns the high-level service, policy, profile, and workspace contracts intended for applications embedding Invariant.

### Plugin-author contracts

Use `invariant.spec` for contracts intended to be referenced by plugin implementations:

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

`invariant.load`, `invariant.catalog`, `invariant.exec`, `invariant.ops`, and unexported objects inside `invariant.spec`/`invariant.api` remain implementation-level surfaces in the 0.1 line. They are available for advanced integration, but they are not yet covered by the same compatibility promise as the explicit `__all__` facades above.

Invariant ships a `py.typed` marker so type checkers can consume its inline annotations.

## Architecture

```text
invariant/
├── spec/       IDs, metadata, manifests, dependencies, version semantics
├── load/       discovery, source loading, isolated proxy, plugin environments
├── catalog/    descriptor registry and version resolution
├── exec/       lifecycle, dependency wiring, transactional activation, capabilities
├── api/        PluginService and policy-facing API
└── ops/        host, daemon, supervisor, tracing, metrics, watchdog
```

The normal flow is:

```text
source discovery
      ↓
PluginDescriptor
      ↓
PluginRegistry
      ↓
DependencyResolver
      ↓
ExecutionRuntime
      ↓
policy-scoped capabilities
      ↓
commit / rollback / shutdown
```

See [`docs/core.md`](docs/core.md) for the runtime boundary and [`CAPABILITIES.md`](CAPABILITIES.md) for the capability contract.

## Version semantics

Invariant owns its concrete-version semantics instead of delegating them to a third-party runtime package. Supported forms include:

- numeric releases such as `1`, `1.2`, and `1.2.3.4`
- epochs such as `2!1.0`
- prereleases (`a`, `b`, `rc`, plus accepted aliases)
- post releases
- dev releases
- local labels

Release zeroes are equivalent for ordering (`1 == 1.0 == 1.0.0`), while registry keys are padded to at least three release components. Version specifier expressions and dependency ranges are not part of this concrete-version type.

## Security model

Capabilities are an API and policy boundary; they are **not** a complete sandbox boundary.

In-process plugins are trusted code. Process isolation used by isolated plugin execution reduces coupling and failure propagation, but it should not be treated as a sufficient security boundary for hostile code. Untrusted or marketplace plugins require a stronger external isolation layer with explicit filesystem, network, resource, secret, and process controls.

See [`SECURITY.md`](SECURITY.md) for the security boundary and reporting guidance.

## Runtime dependency contract

Production code under `invariant/` may import only:

1. Python standard-library modules, and
2. other `invariant` modules.

The core test suite contains an AST-based regression check for this rule. Test/build tooling may use development dependencies; those are not runtime dependencies.

## Source status

`ManifestSource` and `FilesystemSource` are implemented. `EntrypointSource`, `ZipSource`, and `RemoteSource` remain incomplete/stubbed and should not be described as production-ready discovery backends yet.

## History

Invariant originated inside **Project Invariant** on January 31, 2026 as the reusable runtime beneath the first product built on the plugin system. In March 2026 the core was deliberately separated from product-specific agent contracts and reused by **Character_OS**, then by **Runplane**. Those descendant copies validated the product/core boundary and also accumulated a small number of independent improvements.

In August 2026 the surviving runtime lineages were reconciled into this standalone canonical repository. Historical commits are intentionally preserved so the repository reflects that evolution rather than presenting the runtime as a new one-shot import.

## License

Apache License 2.0. See [`LICENSE`](LICENSE).
