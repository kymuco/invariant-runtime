# Manifest v1 reference

This page defines `manifest.json` files consumed by Invariant's manifest source in the 0.1 line.

If you are building a first plugin, start with [Getting started](../getting-started.md). Use this page when you need exact fields, defaults, or validation rules.

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

`PluginService.from_manifest_paths(...)` uses strict discovery, so `version` is required on the recommended host path.

## Defaults at a glance

| Field | Default |
| --- | --- |
| `maturity` | `stable` |
| `priority` | `0` |
| dependency `scope` | `EXECUTION` |
| dependency `optional` | `false` |
| `deps.mode` | `none` |
| `deps.only_binary` | `true` |

Fields not listed as required can be omitted until the plugin needs them.

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
| `maturity` | string | no | `experimental`, `beta`, `stable`, or `deprecated`. |
| `priority` | integer | no | Plugin priority metadata. |
| `categories` | array of strings | no | Category metadata. |
| `tags` | array of strings | no | Tag metadata. |
| `dependencies` | array of objects | no | Invariant plugin or capability dependencies. |
| `runtime` | object | no | Runtime timeout and configuration metadata. |
| `deps` | object | no | Python environment dependency and isolation configuration. |

Unknown top-level fields are rejected because Manifest v1 metadata is constructed from the documented schema. Do not use undocumented extension fields at the top level.

## `source`

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

The resolved source file must exist and remain under the plugin root. Path traversal outside the plugin directory is rejected.

### `source.class_name`

A non-empty class name found in the source module. The class must inherit from `BasePlugin`.

## Plugin dependencies: `dependencies`

`dependencies` declares dependencies on other Invariant plugins or capabilities.

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

Each dependency object supports:

| Field | Type | Required | Default | Meaning |
| --- | --- | --- | --- | --- |
| `id` | string | yes | — | Plugin or capability reference. |
| `param` | string | yes | — | Valid Python identifier used as the injection keyword. |
| `scope` | string | no | `EXECUTION` | `SINGLETON`, `EXECUTION`, `PLUGIN`, or `TRANSIENT`. |
| `optional` | boolean | no | `false` | Whether missing resolution may yield `None`. |

Dependency parameter names must be unique within a plugin.

`dependencies` is not the place for third-party Python packages. Use `deps` for plugin-environment packages.

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

Each timeout must be a positive number or `null`.

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

`deps` is deliberately separate from `dependencies`:

```text
dependencies  → other Invariant plugins or capabilities
deps          → third-party Python packages for an isolated plugin environment
```

### No isolated environment

Omit `deps`, or write:

```json
{
  "deps": {
    "mode": "none"
  }
}
```

The plugin runs in the host/core Python environment.

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

Process isolation is dependency and failure separation, not a complete hostile-code sandbox. Read [Security](../../SECURITY.md) before using isolated plugins with untrusted code.

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

## Validation and discovery

Manifest discovery validates structure before registration. In particular:

- `manifest_version` must be supported;
- `id`, `source.code_path`, and `source.class_name` must be non-empty;
- strict discovery requires an explicit plugin version;
- source and dependency paths must stay under the plugin root;
- malformed timeout or configuration blocks fail discovery;
- malformed Python isolation metadata fails discovery;
- unknown top-level metadata fields are rejected;
- non-stable plugins are skipped unless the host opts into unstable discovery.

The recommended host constructor defaults to `fail_fast=True`, so malformed manifests are surfaced during startup instead of being silently ignored.

## See also

- [Getting started](../getting-started.md)
- [Plugin authoring](../guides/plugin-authoring.md)
- [Runtime model](../concepts/runtime-model.md)
- [Capabilities](../../CAPABILITIES.md)
