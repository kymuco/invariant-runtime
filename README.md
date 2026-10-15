<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/invariant-hero-dark.svg">
  <img src="docs/assets/invariant-hero-light.svg" alt="Invariant Runtime — extensibility with boundaries; discover, resolve, activate, release" width="100%">
</picture>

# Invariant Runtime

**A dependency-free Python plugin runtime. Explicit contracts. Owned lifecycle.**

Invariant is a small, embeddable runtime for applications that need versioned plugins, declared dependencies, policy-governed capabilities, and predictable runtime ownership—without adopting a larger framework.

[![CI](https://github.com/kymuco/invariant-runtime/actions/workflows/ci.yml/badge.svg)](https://github.com/kymuco/invariant-runtime/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-455554)](pyproject.toml)
[![Apache--2.0](https://img.shields.io/badge/license-Apache--2.0-A75B2C)](LICENSE)

[**Get started**](docs/getting-started.md) · [**Runtime model**](docs/concepts/runtime-model.md) · [**Public API**](docs/reference/public-api.md) · [**Documentation**](docs/index.md)

## A small core for a difficult boundary

Discovering Python modules is easy. Owning their identity, activation, dependencies, authority, and cleanup as an application grows is harder.

| Boundary | What Invariant provides |
| :-- | :-- |
| **Identity** | Versioned manifest discovery and deterministic selection |
| **Dependencies** | Explicit plugin and capability requirements |
| **Authority** | Policy-scoped handles for governed host resources |
| **Ownership** | Managed and execution-scoped lifecycles, cleanup and runtime-owned rollback |
| **Integration** | A narrow host-facing `invariant.api` and plugin-facing `invariant.spec` |

**No mandatory third-party runtime dependencies.** Invariant itself uses the Python standard library. Individual plugins may use other Python packages; the runtime does not make those packages mandatory for every host.

## First plugin

From a source checkout with Python **3.11+**:

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

The host-facing integration can be as small as:

```python
from invariant.api import PluginService

service = PluginService.from_manifest_paths(["examples"])
try:
    hello = service.get("example:hello")
    print(hello.greet("Invariant"))
finally:
    service.close()
```

Build the plugin from three files in the [step-by-step guide](docs/getting-started.md). The guide uses an executable example checked against the source.

## How it fits together

```text
                  APPLICATION / HOST
                       │
                PluginService
                       │
            manifest discovery
                       ▼
           versioned dependency graph
                       ▼
         policy-scoped capability wiring
                       ▼
           activation / owned lifecycle
                       │
                 PLUGIN CODE
```

Plugin behavior stays ordinary Python. The host owns its policy and application decisions; Invariant owns the runtime mechanisms around them.

**This is not a hostile-code sandbox.** In-process plugins are trusted Python; process isolation and capability policies do not by themselves secure untrusted plugins. See the [security model](SECURITY.md). Runtime-owned rollback also cannot undo arbitrary external side effects.

## Pick your path

| I want to… | Start with |
| :-- | :-- |
| Write my first plugin | [Getting started](docs/getting-started.md) |
| Understand the boundaries | [Runtime model](docs/concepts/runtime-model.md) |
| Embed Invariant in a host | [Public API](docs/reference/public-api.md) |
| Describe a plugin precisely | [Manifest v1](docs/reference/manifest-v1.md) |
| Give a plugin governed resources | [Capabilities](CAPABILITIES.md) |
| Contribute a fix or documentation | [Contributing](CONTRIBUTING.md) |
| Understand scope and future work | [Project status](docs/project/status.md) |

## Status and compatibility

**0.1.0 alpha on `main`** · Python 3.11+ · Apache-2.0.

The supported 0.x public imports are `invariant.api`, `invariant.spec`, and the root package's `__version__`. Internal layers (`invariant.load`, `invariant.catalog`, `invariant.exec`, `invariant.ops`) may evolve without the same compatibility promise.

`ManifestSource` and `FilesystemSource` are implemented. Entrypoint, ZIP, and remote discovery are incomplete and not production-ready. Alpha status is a compatibility warning, not a claim that every advanced use case is supported.

[Public API reference](docs/reference/public-api.md) · [Security](SECURITY.md) · [Contributing](CONTRIBUTING.md) · [Apache-2.0 license](LICENSE)
