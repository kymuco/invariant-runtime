# Invariant Runtime

A dependency-free Python runtime for versioned plugins, explicit dependencies, governed capabilities and managed lifecycle. Invariant is a library to embed in an application, not an application framework.

## Start here

[Getting started](getting-started.md) walks through a complete plugin class, manifest and host.

From a source checkout with Python 3.11+:

```bash
python -m pip install .
python examples/hello_plugin/run.py
```

Expected output:

```text
Hello, Invariant!
```

## Embed the runtime

Application code uses `invariant.api`:

```python
from invariant.api import PluginService

service = PluginService.from_manifest_paths(["plugins"])
try:
    plugin = service.get("example:hello")
    print(plugin.greet("Invariant"))
finally:
    service.close()
```

The plugin class itself uses `invariant.spec`. [Getting started](getting-started.md) includes the files needed to make this example run; do not copy the host snippet without creating the matching manifest.

## Documentation by task

| Goal | Read |
| :-- | :-- |
| Write and run a first plugin | [Getting started](getting-started.md) |
| Understand plugins, manifests, service and capabilities | [Runtime model](concepts/runtime-model.md) |
| Develop a plugin beyond the first example | [Plugin authoring](guides/plugin-authoring.md) |
| Declare dependencies and governed access | [Dependencies and capabilities](guides/dependencies-and-capabilities.md) |
| Embed the supported host interface | [Public API](reference/public-api.md) |
| Validate an exact manifest | [Manifest v1](reference/manifest-v1.md) |
| Inspect built-in capability contracts | [Capabilities](reference/capabilities.md) |
| Contribute or report a problem | [Contributing](project/contributing.md) |
| Check alpha status and limitations | [Project status](project/status.md) |
| Understand the trust boundary | [Security](security.md) |
| Work on implementation internals | [Core architecture](core.md) |

## Boundaries to know

Invariant's production runtime declares **no mandatory third-party dependencies**. An individual plugin may use its own packages, and optional development/documentation environments have separate dependencies.

The supported host-facing API is `invariant.api`, the plugin-author surface is `invariant.spec`, and the root package exposes `__version__`. The remaining implementation modules are not stable public interfaces in the 0.1 line.

**Policy-governed capabilities are not a hostile-code sandbox.** In-process plugins are trusted code; worker isolation is not a complete security boundary. Activation rollback covers runtime-owned state, not arbitrary external effects. See [Security](security.md).

Invariant is **0.1 alpha**: versioned manifest and filesystem discovery are implemented, while entrypoint, ZIP and remote discovery paths are incomplete. Read [Project status](project/status.md) before relying on advanced features.

This site uses ordinary Markdown and the documentation theme's default layout. Source examples and links are checked against the repository.
