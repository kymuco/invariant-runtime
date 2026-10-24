# Plugin Catalog v1 (internal)

Plugin Catalog v1 is an **inert, locally parsed list of pinned installation
offers**, consumed by the existing `PluginInstallationPlanner`. This is an
internal `invariant.load` integration, not an `invariant.api` public API,
marketplace, discovery backend, dependency solver, or approval UI.

## Exact JSON contract

```json
{
  "schema_version": 1,
  "artifacts": [
    {
      "ref": "example:audio-core@1.0.0",
      "url": "https://plugins.example/audio-core.zip",
      "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "dependencies": []
    },
    {
      "ref": "example:voice-a@1.0.0",
      "url": "https://plugins.example/voice-a.zip",
      "sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
      "dependencies": ["example:audio-core@1.0.0"]
    }
  ]
}
```

The sample SHA-256 values are **placeholders**, not verified artifact digests.
Every artifact must declare all four fields. `ref` is a concrete plugin
`id@version`; `dependencies` contains only mandatory, exact-version
**plugin** dependencies. Optional plugin dependencies and capabilities are
excluded. A plugin's Python-package `deps` are not part of this contract.

No extra fields, duplicate JSON keys, duplicate canonical plugin references,
non-finite constants, or ambiguous dependency entries are accepted. Input is
limited to **1 MiB**, **256 artifacts** and **64 required dependencies per
artifact**. Invalid or non-UTF-8 JSON fails closed. Unknown schema versions
are rejected rather than guessed.

## Verification and usage

```python
from invariant.load.plugin_catalog import VerifiedPluginCatalog

catalog = VerifiedPluginCatalog.from_bytes(
    catalog_bytes,
    expected_sha256=trusted_catalog_sha256,
)
planner = catalog.planner(
    store,
    allowed_origins={"https://plugins.example"},
)
plan = planner.plan(["example:voice-a@1.0.0"])

# Host presents downloads/installed components, gathers actual approval,
# and only then calls:
result = planner.apply(plan, approved_plan_id=plan.plan_id)
```

The **expected catalog SHA-256 must come from an independently trusted source**,
not from the downloaded catalog or the untrusted server that served it.
This proves only that the catalog bytes match an approved digest; it is **not**
publisher signing, revocation, freshness, package safety, or proof that a
publisher is trustworthy. Fetching catalogs, key management, update policy,
and any marketplace ranking/entitlements are host or higher-layer concerns.

The catalog never performs networking itself. Host-approved origins are still
checked by `PluginInstallationPlanner`; ZIP downloads use the existing
HTTPS/TLS/SHA limits and their real manifests must match the approved
dependency graph. The host owns every decision to install or grant capabilities.
Neither `plan()` nor catalog parsing authorizes work.

The catalog is an immutable snapshot. For an update, verify new bytes using
a separately trusted new expected digest and obtain a **new plan approval**.
The Store's own catalog (installed-object index) is a different concept:
that local index is not this distribution catalog.

Related: [Local plugin ZIPs](../guides/local-plugin-archives.md),
[Manifest v1](manifest-v1.md), [Security](../security.md).
