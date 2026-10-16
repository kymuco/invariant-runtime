# Invariant Runtime

**Extensibility, with boundaries.**

Build extensible Python applications from independent, composable plugins. Invariant manages discovery, dependencies, activation, and lifecycle, while your application stays in control of its behavior and access to host resources.

[![CI](https://github.com/kymuco/invariant-runtime/actions/workflows/ci.yml/badge.svg)](https://github.com/kymuco/invariant-runtime/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/Python%203.11%2B-3776AB?style=flat&logo=python&logoColor=white)](https://www.python.org/downloads/)
[![0.1 alpha](https://img.shields.io/badge/0.1.1a1%20alpha-64748B?style=flat)](docs/project/status.md)
[![Apache-2.0](https://img.shields.io/badge/Apache--2.0-64748B?style=flat)](LICENSE)

No mandatory third-party runtime dependencies. Plugins may use their own packages.  
**This branch stages 0.1.1a1 as an unpublished alpha candidate.**

## Quick start

Run a small application that imports order data through two independently versioned plugins:

```bash
git clone https://github.com/kymuco/invariant-runtime.git
cd invariant-runtime
python -m pip install .
python examples/data_importers/run.py
```

```text
orders.csv: 2 items, total $30.25 via example:orders-csv
orders.json: 2 items, total $35.50 via example:orders-json
```

The **host** chooses the importer by file extension; Invariant discovers and manages each plugin. Try your own file with `python examples/data_importers/run.py path/to/orders.csv`.

To see a third format plugged in **without changing the host**, run the included TSV add-on:

```bash
python examples/data_importers/run.py --plugin-dir examples/data_importers/addons examples/data_importers/samples/orders.tsv
```

```text
orders.tsv: 1 item, total $15.00 via example:orders-tsv
```

[Inspect the complete example](examples/data_importers/README.md) to see its plugin manifests and host-defined interface.

Want the simplest possible plugin? [Build Hello Plugin in three files](docs/getting-started.md). For dependencies and governed resources, try [Capability Notes](examples/capability_notes/README.md).

## Why a plugin runtime?

A plugin is more than an imported module or an operation to call: it is an independently maintained component with an identity, dependencies, and a lifetime. Invariant provides the infrastructure to compose and manage these components without baking each implementation into the application.

For a small, fixed set of modules, ordinary Python imports are usually simpler. Invariant becomes useful when plugins evolve independently and need explicit dependency resolution, predictable cleanup, or governed access to host resources.

| Need | What Invariant provides |
| --- | --- |
| Find and select extensions | Versioned manifests and deterministic discovery |
| Compose components | Explicit plugin and capability dependencies |
| Own component lifetime | Managed or execution-scoped activation and cleanup |
| Govern host resources | Policy-scoped filesystem, HTTP, process, configuration, and other capability handles |

## Where it fits

- **Extensible applications:** a CLI, desktop application, or service with independently maintained importers, processors, or integrations. The host chooses which implementations to use; Invariant manages their plugin lifecycle and dependencies.
- **Resource-governed components:** a plugin depends on another plugin that requests a host filesystem capability. The host's execution policy can provide or deny that capability without changing plugin code.
- **Modular AI and media applications:** model providers, speech backends, and storage adapters can be plugins. Invariant does not implement model reasoning, agent tool selection, or a protocol such as MCP.

These are architectural examples, not bundled applications or promises of automatic implementation switching. See [Use cases](docs/use-cases.md) for the boundaries of each scenario.

## The host stays small

The host uses the public API to select a plugin; the plugin's behavior remains ordinary Python:

```python
from invariant.api import PluginService

service = PluginService.from_manifest_paths(["examples/data_importers/plugins"])
try:
    importer = service.get("example:orders-csv")
    orders = importer.load("examples/data_importers/samples/orders.csv")
finally:
    service.close()
```

[Getting started](docs/getting-started.md) explains the class and manifest step by step.

## Dependencies and governed resources

The existing [capability_notes example](examples/capability_notes/README.md) contains a real dependency graph:

```text
example:notes@1.0.0
  -> example:notes-storage@1.0.0
       -> invariant:capability.fs_sandbox@0.3.0
```

Run it from the repository root:

```bash
python examples/capability_notes/run.py
```

The host supplies a policy for that execution. The dependency graph activates when the required filesystem capability is available; an explicitly denied capability prevents activation. The plugin implementations do not need to change.

## Limits and trade-offs

- **Not needed for every app.** Plain Python imports or simple dependency injection are often sufficient for static components.
- **Not a hostile-code sandbox.** In-process plugins are trusted Python; even isolated workers are not a complete security boundary.
- **Not a general hot-swap engine.** Automatic replacement of live components and transfer of their state are not supported public contracts.
- **Not transactional business logic.** Runtime-owned activation rollback cannot undo arbitrary external side effects.
- **Alpha contract.** Public interfaces can evolve before 1.0. Manifest and filesystem discovery are implemented; entrypoint, ZIP, and remote loaders are not production-ready.

## Documentation

| Goal | Read |
| --- | --- |
| Install and build the first plugin | [Getting started](docs/getting-started.md) |
| See architectural application scenarios | [Use cases](docs/use-cases.md) |
| Understand the lifecycle | [Runtime model](docs/concepts/runtime-model.md) |
| Author plugins and declare dependencies | [Plugin authoring](docs/guides/plugin-authoring.md) |
| Work with capabilities and execution policy | [Dependencies and capabilities](docs/guides/dependencies-and-capabilities.md) |
| Embed the supported host API | [Public API](docs/reference/public-api.md) |
| Verify capability contracts | [Capabilities](CAPABILITIES.md) |
| Read the alpha guarantees | [Project status](docs/project/status.md) |
| Check trust assumptions | [Security](SECURITY.md) |
| Report issues or contribute | [Contributing](CONTRIBUTING.md) |

See the [documentation index](docs/index.md) for the full map.

## License

[Apache License 2.0](LICENSE).
