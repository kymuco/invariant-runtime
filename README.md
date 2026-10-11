# Invariant Runtime

**Extensibility, with boundaries.**

Invariant is a small, standard-library-only Python runtime for versioned plugins, explicit dependencies, policy-scoped capabilities, and owned lifecycle. It helps applications add capabilities without moving product logic into the runtime.

Python 3.11+ &nbsp; · &nbsp; No mandatory third-party runtime dependencies &nbsp; · &nbsp; 0.1.0 alpha &nbsp; · &nbsp; Apache-2.0

- **Versioned discovery** — explicit manifests identify plugins, versions, and sources.
- **Dependency resolution** — plugin and capability requirements are declared rather than inferred.
- **Governed host access** — capabilities are handles supplied according to host policy.
- **Owned lifecycle** — the runtime manages activation, cleanup, and rollback of runtime-owned state.

> Invariant is infrastructure, not an agent or application framework. Workflows, companions, approval, marketplaces, and product decisions belong to the application using it.

## Try it

From a source checkout with Python 3.11 or newer (no test dependencies needed):

```bash
python -m pip install .
python examples/hello_plugin/run.py
```

Expected output:

```text
Hello, Invariant!
```

The supported host-facing API is intentionally small:

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

## Choose your starting point

| Goal | Read |
| --- | --- |
| Add a capability by writing a plugin | [Getting started](docs/getting-started.md) |
| Extend an existing Python application | [Public API](docs/reference/public-api.md) |
| Understand the mental model | [Runtime model](docs/concepts/runtime-model.md) |
| Design a production plugin | [Plugin authoring](docs/guides/plugin-authoring.md) |
| Define or validate a manifest | [Manifest v1](docs/reference/manifest-v1.md) |
| Work with host resources | [Capabilities](CAPABILITIES.md) |
| Understand trust and isolation | [Security](SECURITY.md) |
| Modify the runtime itself | [Core architecture](docs/core.md) |

The **[documentation home](docs/index.md)** contains the complete map.

## Public surface

Invariant 0.1 keeps its supported imports deliberately narrow:

```python
from invariant import __version__       # package metadata
from invariant.api import PluginService  # host-facing API
from invariant.spec import BasePlugin    # plugin-author API
```

Application code should prefer `invariant.api`. Plugin code should prefer `invariant.spec`.

`invariant.load`, `invariant.catalog`, `invariant.exec`, `invariant.ops`, and unexported objects inside the public packages remain implementation-level surfaces in the 0.1 line. See the [Public API reference](docs/reference/public-api.md) for the complete compatibility contract.

The package ships a `py.typed` marker for PEP 561 inline typing support.

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
