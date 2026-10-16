# Invariant Runtime

**A dependency-free plugin runtime for Python 3.11+.**

[![CI](https://github.com/kymuco/invariant-runtime/actions/workflows/ci.yml/badge.svg)](https://github.com/kymuco/invariant-runtime/actions/workflows/ci.yml)

Python 3.11+ · 0.1 alpha · Apache-2.0  
No mandatory third-party runtime dependencies.

Invariant helps Python applications discover, resolve, activate, and manage **versioned plugins**. It handles plugin infrastructure; your application owns the interfaces, behavior, and decisions about what to run.

## Why a plugin runtime?

Direct imports are usually the right choice for a small, fixed collection of modules. Once independently developed extensions need identities, versions, declared dependencies, predictable initialization and cleanup, or host-governed resources, every application would otherwise need to build those mechanisms itself.

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

## Quick start

From a source checkout (no package-registry release required):

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

The checked example uses the public host API:

```python
from invariant.api import PluginService

service = PluginService.from_manifest_paths(["examples"])
try:
    hello = service.get("example:hello")
    print(hello.greet("Invariant"))
finally:
    service.close()
```

[Getting started](docs/getting-started.md) shows the matching plugin class and manifest. Plugin authors use `invariant.spec`; hosts use `invariant.api`.

## Beyond Hello World

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
