# Invariant Runtime

**A small, policy-aware plugin runtime for Python.**

Invariant lets a Python application discover versioned plugins, resolve dependencies, govern host capabilities, and own plugin lifecycle without pulling product behavior into the runtime.

- **Zero mandatory runtime dependencies** — production code uses only the Python standard library.
- **Explicit contracts** — plugins declare identity, version, dependencies, and source metadata.
- **Governed host access** — capabilities are exposed through policy-controlled interfaces.
- **Deterministic lifecycle** — discovery, version selection, activation, rollback, and shutdown are runtime concerns.

> Invariant is infrastructure, not an application framework. Domain behavior, agents, companions, workflows, marketplace logic, and product UX belong outside the runtime.

## Try it

From the repository root:

```bash
python -m pip install -e ".[test]"
python examples/hello_plugin/run.py
```

Expected output:

```text
Hello, Invariant!
```

The host-facing path is intentionally small:

```python
from invariant.api import PluginService

service = PluginService.from_manifest_paths(["examples"])
try:
    hello = service.get("example:hello")
    print(hello.greet("Invariant"))
finally:
    service.close()
```

Build the same plugin from an empty directory in **[Getting started](docs/getting-started.md)**.

## Where to go next

| Goal | Read |
| --- | --- |
| Build your first plugin | [Getting started](docs/getting-started.md) |
| Understand the mental model | [Runtime model](docs/concepts/runtime-model.md) |
| Design a production plugin | [Plugin authoring](docs/guides/plugin-authoring.md) |
| Embed Invariant in an application | [Public API](docs/reference/public-api.md) |
| Define or validate a manifest | [Manifest v1](docs/reference/manifest-v1.md) |
| Work with host resources | [Capabilities](CAPABILITIES.md) |
| Understand trust and isolation | [Security](SECURITY.md) |
| Modify the runtime itself | [Core architecture](docs/core.md) |

The **[documentation home](docs/index.md)** contains the complete map.

## Public surface

Invariant 0.1 deliberately keeps the supported import surface narrow:

```python
from invariant import __version__       # package metadata
from invariant.api import PluginService  # host-facing API
from invariant.spec import BasePlugin    # plugin-author API
```

Application code should prefer `invariant.api`. Plugin code should prefer `invariant.spec`.

`invariant.load`, `invariant.catalog`, `invariant.exec`, `invariant.ops`, and unexported objects inside the public packages remain implementation-level surfaces in the 0.1 line. See the [Public API reference](docs/reference/public-api.md) for the complete compatibility contract.

Invariant also ships a `py.typed` marker for PEP 561 inline typing support.

## Runtime model

```text
manifest / source
       │
       ▼
   discovery
       │
       ▼
version selection
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

The public host API keeps these internal layers behind `PluginService`. Read the [Runtime model](docs/concepts/runtime-model.md) when you need the details.

## Security boundary

Capabilities are an **API and policy boundary**, not a complete hostile-code sandbox.

In-process plugins are trusted code. Isolated plugin processes provide dependency and failure separation, but hostile or marketplace code requires stronger external isolation with explicit filesystem, network, resource, process, and secret controls.

Read [SECURITY.md](SECURITY.md) before designing an untrusted-plugin system.

## Status

Invariant Runtime is currently **0.1.0 / alpha**.

`ManifestSource` and `FilesystemSource` are implemented. `EntrypointSource`, `ZipSource`, and `RemoteSource` are incomplete and are not presented as production-ready discovery backends.

Invariant originated inside **Project Invariant** on January 31, 2026, was later reused by **Character_OS** and **Runplane**, and was reconciled into this standalone canonical repository in August 2026. Historical commits are preserved intentionally.

## License

Apache License 2.0. See [LICENSE](LICENSE).
