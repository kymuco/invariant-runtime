# Getting started

Build and run your first Invariant plugin from an empty directory.

By the end, you will have:

```text
hello_plugin/
├── manifest.json
├── plugin.py
└── run.py
```

and this output:

```text
Hello, Invariant!
```

## Requirements

- Python 3.13 or newer
- Invariant Runtime installed in your environment

From a source checkout:

```bash
python -m pip install .
```

## 1. Write the plugin

Create `hello_plugin/plugin.py`:

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

A plugin is ordinary Python code with a small lifecycle contract:

- `initialize(...)` runs when the runtime activates the plugin.
- `shutdown()` runs when the runtime releases it.
- your own methods, such as `greet()`, define the behavior your host uses.

`BasePlugin` comes from `invariant.spec`, the supported plugin-author namespace.

## 2. Describe it with a manifest

Create `hello_plugin/manifest.json`:

```json
{
  "manifest_version": 1,
  "id": "example:hello",
  "version": "1.0.0",
  "name": "Hello Plugin",
  "description": "A minimal Invariant plugin.",
  "maturity": "stable",
  "source": {
    "code_path": "plugin.py",
    "class_name": "HelloPlugin"
  }
}
```

The manifest answers five questions:

1. Which manifest contract is this? `manifest_version`.
2. What is the plugin called? `id`.
3. Which concrete version is this? `version`.
4. Where is the code? `source.code_path`.
5. Which class should Invariant instantiate? `source.class_name`.

Everything else is optional until your plugin needs it.

## 3. Create the host

Create `hello_plugin/run.py`:

```python
from pathlib import Path

from invariant.api import PluginService


plugin_dir = Path(__file__).resolve().parent
service = PluginService.from_manifest_paths([plugin_dir])

try:
    hello = service.get("example:hello")
    print(hello.greet("Invariant"))
finally:
    service.close()
```

Run it:

```bash
python hello_plugin/run.py
```

Expected output:

```text
Hello, Invariant!
```

## What just happened

```text
manifest.json
     │
     ▼
discover plugin metadata
     │
     ▼
register example:hello@1.0.0
     │
     ▼
service.get("example:hello")
     │
     ▼
load HelloPlugin
     │
     ▼
initialize(...)
     │
     ▼
hello.greet(...)
     │
     ▼
service.close() → shutdown()
```

The host does not need to assemble loader, registry, or source objects itself. `PluginService.from_manifest_paths(...)` keeps those implementation details behind the supported host facade.

## Add behavior, not runtime plumbing

Your plugin can now grow in three directions:

- declare plugin dependencies when it needs another plugin;
- request policy-governed capabilities when it needs filesystem, network, configuration, or workspace access;
- use isolated Python dependencies when it needs packages that should not live in the host environment.

Those features are deliberately not required for the first plugin.

## Next

- Read [Plugin authoring](guides/plugin-authoring.md) for lifecycle and design guidance.
- Use the [Manifest v1 reference](reference/manifest-v1.md) when you add metadata, dependencies, timeouts, or isolation.
- Read the [Runtime model](concepts/runtime-model.md) to understand what Invariant owns versus what your plugin owns.
- Read [Capabilities](../CAPABILITIES.md) before giving plugins access to host resources.

The repository contains the same checked example under [`examples/hello_plugin`](../examples/hello_plugin/README.md).
