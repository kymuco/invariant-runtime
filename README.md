# Invariant Runtime

**A dependency-free plugin runtime for Python 3.11+.**

[![CI](https://github.com/kymuco/invariant-runtime/actions/workflows/ci.yml/badge.svg)](https://github.com/kymuco/invariant-runtime/actions/workflows/ci.yml)
&nbsp; Python 3.11+ · 0.1 alpha · Apache-2.0 · No mandatory third-party runtime dependencies

Invariant is a small library for applications that need **versioned plugins**, **explicit dependencies**, **policy-governed capabilities**, and **managed lifecycle**. It handles plugin infrastructure without owning application logic.

## Quick start

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

The example uses the supported host-facing API:

```python
from invariant.api import PluginService

service = PluginService.from_manifest_paths(["examples"])
try:
    hello = service.get("example:hello")
    print(hello.greet("Invariant"))
finally:
    service.close()
```

See [Getting started](docs/getting-started.md) to build this plugin, including its class and manifest, from an empty directory.

## Why use it?

Importing a Python module is straightforward. Coordinating plugin versions, declared dependencies, permitted host access and cleanup across a growing application is a separate problem.

| Concern | What Invariant provides |
| :-- | :-- |
| Discovery | Versioned manifests and deterministic plugin selection |
| Dependencies | Explicit plugin and capability requirements |
| Host access | Policy-governed capability handles |
| Lifetime | Managed activation, scoped execution, shutdown and runtime-owned rollback |

**The runtime itself uses only the Python standard library.** Plugins can depend on third-party packages, but Invariant does not make their packages mandatory for every host.

## API and limits

Hosts integrate through `invariant.api`; plugin authors use `invariant.spec`. The package version is exposed as `invariant.__version__`. Modules such as `invariant.load`, `invariant.catalog`, `invariant.exec` and `invariant.ops` remain implementation details in the 0.1 line.

Capability policies are API boundaries, **not a sandbox for hostile Python code**. In-process plugins must be trusted. Runtime-owned rollback does not undo arbitrary side effects performed outside the runtime.

The current `main` branch is **0.1.0 alpha**. `ManifestSource` and `FilesystemSource` are implemented; entrypoint, ZIP and remote discovery are not production-ready.

## Documentation

| Task | Reference |
| :-- | :-- |
| Install and write the first plugin | [Getting started](docs/getting-started.md) |
| Understand the lifecycle model | [Runtime model](docs/concepts/runtime-model.md) |
| Embed the supported API | [Public API](docs/reference/public-api.md) |
| Define plugin metadata | [Manifest v1](docs/reference/manifest-v1.md) |
| Author plugins with dependencies | [Plugin authoring](docs/guides/plugin-authoring.md) |
| Work with governed host resources | [Capabilities](CAPABILITIES.md) |
| Check alpha guarantees and limitations | [Project status](docs/project/status.md) |
| Report bugs or contribute | [Contributing](CONTRIBUTING.md) |
| Understand the trust boundary | [Security](SECURITY.md) |

The [documentation index](docs/index.md) contains the full map.

## License

[Apache License 2.0](LICENSE).
