# Manifest v1 reference

This page is the reference for `manifest.json` files consumed by the manifest source in Invariant 0.1.

For a tutorial, start with [Getting started](../getting-started.md).

## Minimal strict manifest

```json
{
  "manifest_version": 1,
  "id": "example:hello",
  "version": "1.0.0",
  "source": {
    "code_path": "plugin.py",
    "class_name": "HelloPlugin"
  }
}
```

`PluginService.from_manifest_paths(...)` uses strict discovery, so `version` is required for this recommended host path.

## Top-level fields

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `manifest_version` | integer | yes | Manifest contract version. Must be `1`. |
| `id` | string | yes | Stable plugin identity. |
| `version` | string | strict mode | Concrete plugin version. |
| `source` | object | yes | Python source location and class. |
| `name` | string or null | no | Human-readable name. |
| `description` | string or null | no | Human-readable description. |
| `author` | string or null | no | Author metadata. |
| `repo` | string or null | no | Repository metadata. |
| `maturity` | string | no | `experimental`, `beta`, `stable`, or `deprecated`. Defaults to `stable`. |
| `priority` | integer | no | Plugin priority metadata. Defaults to `0`. |
| `categories` | array of strings | no | Category metadata. |
| `tags` | array of strings | no | Tag metadata. |
| `dependencies` | array of objects | no | Invariant plugin or capability dependencies. |
| `runtime` | object | no | Runtime timeout/config metadata. |
| `deps` | object | no | Python environment dependency/isolation configuration. |

Unknown top-level fields are not part of Manifest v1 and may fail metadata construction. Prefer only documented fields.

## `source`

Required fields:

```json
{
  "source": {
    "code_path": "plugin.py",
    "class_name": "HelloPlugin"
  }
}
```

### `source.code_path`

A non-empty path relative to the directory containing `manifest.json`.

The resolved source file must exist and must remain under the plugin root; path traversal outside the plugin directory is rejected.

### `source.class_name`

A non-empty class name found in the source module. The class must inherit from `BasePlugin`.

## Plugin dependencies: `dependencies`

`dependencies` describes dependencies on other Invariant plugins or capabilities.

```json
{
  "dependencies": [
    {
      "id": "acme:storage@1.0.0",
      "param": "storage",
      "scope": "EXECUTION",
      "optional": false
    }
  ]
}
```

Each object supports:

| Field | Type | Required | Default | Meaning |
| --- | --- | --- | --- | --- |
| `id` | string | yes | — | Plugin or capability reference. |
| `param` | string | yes | — | Valid Python identifier used as the injection keyword. |
| `scope` | string | no | `EXECUTION` | `SINGLETON`, `EXECUTION`, `PLUGIN`, or `TRANSIENT`. |
| `optional` | boolean | no | `false` | Whether missing resolution may yield `None`. |

Dependency parameter names must be unique within a plugin.

## Runtime metadata: `runtime`

### Call timeout

```json
{
  "runtime": {
    "call_timeout_s": 10.0
  }
}
```

`call_timeout_s` must be a positive number or `null`.

### Per-method timeouts

```json
{
  "runtime": {
    "method_timeouts": {
      "search": 5.0,
      "refresh": 30.0
    }
  }
}
```

Each value must be a positive number or `null`.

For backward compatibility, `call_timeout_s` and `method_timeouts` are also accepted at the top level. If both forms are present, the nested `runtime` value wins.

### Configuration snapshot metadata

A manifest can declare the parameter name and prefix used for runtime-provided configuration snapshots:

```json
{
  "runtime": {
    "config_param": "config",
    "config_prefix": "acme.search."
  }
}
```

`config_param` must be a valid Python identifier. If `config_prefix` is supplied without `config_param`, the parameter defaults to `config`.

## Python environment dependencies: `deps`

`deps` is different from `dependencies`.

- `dependencies` connects Invariant plugins and capabilities.
- `deps` describes third-party Python packages for plugin isolation.

### No isolated environment

Omit the block, or use:

```json
{
  "deps": {
    "mode": "none"
  }
}
```

The plugin runs in the core Python environment.

### Online isolated environment

```json
{
  "deps": {
    "mode": "online",
    "requirements": "requirements.txt",
    "only_binary": true
  }
}
```

`requirements` is required and must resolve to a file under the plugin root.

Optional pip configuration fields:

- `index_url`
- `extra_index_urls`
- `trusted_hosts`
- `only_binary` (defaults to `true`)

### Bundled isolated environment

```json
{
  "deps": {
    "mode": "bundle",
    "requirements": "requirements.txt",
    "wheels_dir": "wheels"
  }
}
```

Both `requirements` and `wheels_dir` are required. Both paths must exist under the plugin root.

## Complete example

```json
{
  "manifest_version": 1,
  "id": "acme:search",
  "version": "1.2.0",
  "name": "Search",
  "description": "Searches an application-owned index.",
  "maturity": "stable",
  "categories": ["search"],
  "tags": ["index"],
  "dependencies": [
    {
      "id": "acme:storage@1.0.0",
      "param": "storage",
      "scope": "EXECUTION",
      "optional": false
    }
  ],
  "runtime": {
    "call_timeout_s": 15.0,
    "method_timeouts": {
      "search": 5.0
    }
  },
  "source": {
    "code_path": "plugin.py",
    "class_name": "SearchPlugin"
  }
}
```

## Validation and discovery behavior

Manifest discovery validates structural fields before registration. In particular:

- `manifest_version` must be supported;
- `id`, `source.code_path`, and `source.class_name` must be non-empty;
- source and dependency paths must stay under the plugin root;
- malformed timeout/config blocks fail discovery;
- malformed Python isolation metadata fails discovery;
- non-stable plugins are skipped unless the host opts into unstable discovery.

The recommended host constructor defaults to `fail_fast=True`, making malformed manifests visible immediately during startup.

## See also

- [Plugin authoring](../guides/plugin-authoring.md)
- [Runtime model](../concepts/runtime-model.md)
- [Capabilities](../../CAPABILITIES.md)
