<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/invariant-hero-dark.svg">
  <img src="docs/assets/invariant-hero-light.svg" alt="Invariant — a fixed-point motif connecting manifest, plugin, and capability to a small Python runtime" width="100%">
</picture>

# Invariant Runtime

**A small Python runtime for plugins with identities, dependencies, permissions, and lifetimes.**

[![CI](https://github.com/kymuco/invariant-runtime/actions/workflows/ci.yml/badge.svg)](https://github.com/kymuco/invariant-runtime/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-5841A0)](pyproject.toml)
[![Apache--2.0](https://img.shields.io/badge/license-Apache--2.0-267F73)](LICENSE)

[Get started](docs/getting-started.md) · [How it works](docs/concepts/runtime-model.md) · [Public API](docs/reference/public-api.md) · [Documentation](docs/index.md)

## A module can be imported. A plugin needs an owner.

A Python import tells you where the code is. It doesn't tell you which version the application intended, who supplies dependencies, which host resources the plugin may use, or what should be cleaned up if activation fails.

Invariant handles those runtime concerns while leaving the application's behavior and decisions to the application. It isn't an agent framework, and it doesn't dictate how your product works.

**The core has zero mandatory third-party runtime dependencies.** It's written against the Python 3.11+ standard library. Plugins can still have dependencies of their own.

## Try a plugin

From a source checkout:

```bash
git clone https://github.com/kymuco/invariant-runtime.git
cd invariant-runtime
python -m pip install .
python examples/hello_plugin/run.py
```

Expected output:

```text
Hello, Invariant!
```

A host uses the public `PluginService` facade:

```python
from invariant.api import PluginService

service = PluginService.from_manifest_paths(["examples"])
try:
    plugin = service.get("example:hello")
    print(plugin.greet("Invariant"))
finally:
    service.close()
```

You can build this example from scratch in [Getting started](docs/getting-started.md). Both the tutorial and its sample files are covered by documentation tests.

## What the runtime owns

| Concern | Runtime contract |
| :-- | :-- |
| Discovery | Manifests identify plugins and concrete versions. |
| Resolution | Declared plugin and capability dependencies are wired explicitly. |
| Access | Host policy governs the capability handles made available. |
| Lifecycle | Activation, managed execution, cleanup, and rollback of runtime-owned state. |

The host decides *whether* to invoke a plugin and *what* work to ask of it. Invariant is responsible for the plugin machinery around that decision.

Capabilities are **policy-controlled APIs, not a hostile-code sandbox**. In-process plugins remain trusted Python code. Runtime-owned rollback cannot undo arbitrary external side effects. Read [Security](SECURITY.md) for the exact limits.

## Find the right reference

| You're working on… | Read |
| :-- | :-- |
| Your first manifest and plugin | [Getting started](docs/getting-started.md) |
| A host application | [Public API](docs/reference/public-api.md) |
| A plugin with dependencies | [Plugin authoring](docs/guides/plugin-authoring.md) |
| Resource access and policies | [Capabilities](CAPABILITIES.md) |
| IDs and version syntax | [Manifest v1](docs/reference/manifest-v1.md) |
| Internal architecture | [Core architecture](docs/core.md) |
| A contribution | [CONTRIBUTING.md](CONTRIBUTING.md) |

## Status

**0.1.0 alpha on `main`** · Python 3.11+ · Apache-2.0.

Only `invariant.api`, `invariant.spec`, and the root `__version__` are explicit public compatibility surfaces for 0.1. `invariant.load`, `invariant.catalog`, `invariant.exec`, and `invariant.ops` are internal. `ManifestSource` and `FilesystemSource` are implemented; entrypoint, ZIP, and remote sources are incomplete.

See [Project status](docs/project/status.md) for what the alpha does and doesn't promise, and [LICENSE](LICENSE) for redistribution terms.
