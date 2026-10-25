# Invariant Runtime

**Extensibility, with boundaries.** Invariant is an embeddable Python runtime for composing applications from independent, versioned plugins. The runtime manages dependencies, activation and lifecycle; applications keep control of domain behavior, permissions and host resources.

## Start here

Install the published alpha from [PyPI](https://pypi.org/project/invariant-runtime/0.1.2a1/) using Python 3.11+:

```bash
python -m pip install invariant-runtime==0.1.2a1
```

The explicit version pins the alpha. No checkout is needed to use Invariant in your own application.

To try the included order-importer example, clone the repository for the example files and run the host with the installed PyPI package. Two versioned plugins parse CSV and JSON, and the host chooses the implementation:

```bash
git clone https://github.com/kymuco/invariant-runtime.git
cd invariant-runtime
python examples/data_importers/run.py
```

Expected output:

```text
orders.csv: 2 items, total $30.25 via example:orders-csv
orders.json: 2 items, total $35.50 via example:orders-json
```

For the smallest plugin built step by step, follow [Getting started](getting-started.md). The [full runnable example](https://github.com/kymuco/invariant-runtime/tree/main/examples/data_importers) shows how to add new formats without modifying the host.

## Embed the runtime

Application code uses `invariant.api`:

```python
from invariant.api import PluginService

service = PluginService.from_manifest_paths(["examples/data_importers/plugins"])
try:
    importer = service.get("example:orders-csv")
    orders = importer.load("examples/data_importers/samples/orders.csv")
    print(len(orders))
finally:
    service.close()
```

This snippet works from the checkout root after installation. Plugins import from `invariant.spec`; [Getting started](getting-started.md) explains the class and manifest.

## Documentation by task

| Goal | Read |
| :-- | :-- |
| Write and run a first plugin | [Getting started](getting-started.md) |
| Decide where the runtime fits | [Use cases](use-cases.md) |
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
