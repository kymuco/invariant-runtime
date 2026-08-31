# Invariant Runtime

**A small, policy-aware plugin runtime for Python.**

Invariant gives applications a neutral runtime for discovering versioned plugins, resolving dependencies, controlling capabilities, and managing lifecycle without tying the host to a product framework.

- **Zero mandatory runtime dependencies** — production code uses only the Python standard library.
- **Explicit boundaries** — plugins declare identity, version, dependencies, and runtime metadata.
- **Governed execution** — capabilities are exposed through policy-controlled interfaces.
- **Deterministic lifecycle** — activation, rollback, shutdown, and version selection are runtime concerns.

> Invariant is infrastructure, not an application framework. Domain behavior, agents, companions, workflows, and marketplace policy belong outside the runtime.

## Start here

If you want to see Invariant work, run the checked example plugin:

```bash
python -m pip install -e ".[test]"
python examples/hello_plugin/run.py
```

Expected output:

```text
Hello, Invariant!
```

The host uses only the public API:

```python
from invariant.api import PluginService

service = PluginService.from_manifest_paths(["examples"])
try:
    hello = service.get("example:hello")
    print(hello.greet("Invariant"))
finally:
    service.close()
```

The plugin itself imports only plugin-author contracts:

```python
from invariant.spec import BasePlugin


class HelloPlugin(BasePlugin):
    def initialize(self, **deps) -> None:
        pass

    def shutdown(self) -> None:
        pass

    def greet(self, name: str) -> str:
        return f"Hello, {name}!"
```

Read **[Getting started](docs/getting-started.md)** to build the same plugin from an empty directory.

## Documentation

Choose the shortest path for what you are trying to do:

| Goal | Start here |
| --- | --- |
| Run your first plugin | [Getting started](docs/getting-started.md) |
| Understand the runtime | [Runtime model](docs/concepts/runtime-model.md) |
| Write production plugins | [Plugin authoring guide](docs/guides/plugin-authoring.md) |
| Embed Invariant in an application | [Public API](docs/reference/public-api.md) |
| Define a manifest | [Manifest v1 reference](docs/reference/manifest-v1.md) |
| Understand capabilities | [Capabilities contract](CAPABILITIES.md) |
| Understand trust and isolation | [Security](SECURITY.md) |
| Work on Invariant itself | [Core architecture](docs/core.md) |

See the **[documentation home](docs/index.md)** for the complete map.

## Public compatibility surface

Invariant 0.1 keeps its supported import surface intentionally narrow.

### Package metadata

```python
from invariant import __version__
```

### Host-facing API

```python
from invariant.api import PluginService, SandboxLimits, WorkspaceOptions
```

### Plugin-author API

```python
from invariant.spec import BasePlugin, DependencySpec, PluginMeta, Version
```

`invariant.load`, `invariant.catalog`, `invariant.exec`, `invariant.ops`, and unexported objects inside `invariant.api` or `invariant.spec` remain implementation-level surfaces in the 0.1 line. They may evolve without the same compatibility promise as the explicit public facades.

Invariant ships a `py.typed` marker for PEP 561 inline typing support.

## Architecture

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
transactional activation
       │
       ▼
policy-scoped capabilities
       │
       ▼
commit · rollback · shutdown
```

The implementation is split into six layers:

```text
invariant/
├── spec/       plugin-facing contracts, IDs, metadata, versions
├── load/       discovery, source loading, isolated plugin environments
├── catalog/    descriptor registry and version selection
├── exec/       lifecycle, dependency wiring, transactions, capabilities
├── api/        supported host-facing facade
└── ops/        host, daemon, supervision, tracing, metrics
```

## Security boundary

Capabilities are an **API and policy boundary**, not a complete hostile-code sandbox.

In-process plugins are trusted code. Isolated plugin processes reduce coupling and failure propagation, but untrusted or marketplace code requires a stronger external isolation layer with explicit filesystem, network, resource, process, and secret controls.

Read [SECURITY.md](SECURITY.md) before designing an untrusted-plugin system.

## Runtime dependency contract

Production code under `invariant/` may import only:

1. Python standard-library modules, and
2. other `invariant` modules.

The core suite contains an AST regression that enforces this rule.

## Project status

Invariant Runtime is currently **0.1.0 / alpha**.

`ManifestSource` and `FilesystemSource` are implemented. `EntrypointSource`, `ZipSource`, and `RemoteSource` are incomplete and are not presented as production-ready discovery backends.

## History

Invariant originated inside **Project Invariant** on January 31, 2026 as the reusable runtime beneath the first product built on the plugin system. In March 2026 the core was separated from product-specific agent contracts and reused by **Character_OS**, then by **Runplane**.

In August 2026 those surviving runtime lineages were reconciled into this standalone canonical repository. Historical commits are preserved intentionally so the repository reflects that evolution rather than presenting the runtime as a one-shot import.

## License

Apache License 2.0. See [LICENSE](LICENSE).
