# Install local plugin ZIPs

This advanced, **internal integration** guide shows how a host installs a local Invariant plugin ZIP into one shared Store, then opens a managed service against the installed plugins. Unlike the ordinary [Getting started](../getting-started.md) path, these helpers live in `invariant.load`, **not** the supported `invariant.api` public facade in the 0.1 alpha.

## Prepare local archives

A plugin ZIP must contain `manifest.json` at the archive root, with a concrete `id`, `version`, and a `source.code_path` pointing to a file inside the archive. Other files can be included under the same archive root.

For example:

```text
audio-core.zip
├── manifest.json
└── plugin.py

voice-a.zip
├── manifest.json
└── plugin.py
```

The voice plugin's manifest declares the shared dependency:

```json
{
  "dependencies": [
    {
      "id": "example:audio-core@1.0.0",
      "param": "audio_core",
      "scope": "SINGLETON"
    }
  ]
}
```

## Install explicitly, then run

```python
from pathlib import Path

from invariant.load.plugin_store import LocalPluginStore

store = LocalPluginStore(Path(".invariant-plugins"))

# Install the dependency before the plugin that requires it.
store.install_zip(Path("audio-core.zip"))
store.install_zip(Path("voice-a.zip"))

with store.open_service() as service:
    voice = service.get("example:voice-a@1.0.0")
    # Call a method defined by your voice plugin here.
```

**The ZIP filenames are not plugin identities.** `install_zip()` reads the identity and concrete version from each archive's manifest and delegates to the existing `install()` operation. That operation validates the files and dependency closure, then records the installed object. A repeated installation with identical contents is idempotent, even if the ZIP compression or metadata differs.

To verify bytes obtained from an external distributor, supply a SHA-256 value obtained independently of the archive:

```python
store.install_zip(
    Path("voice-a.zip"),
    expected_sha256="<64-character-hex-sha256>",
)
```

Replace the placeholder with the real expected digest; it is not a usable value as written.

If Voice B needs the already-installed `example:audio-core@1.0.0`, install its own archive into the **same Store**. The existing Audio Core object is reused rather than extracted a second time. Different concrete versions may coexist. Missing required plugin dependencies fail installation; there is **no automatic download**, network request, marketplace lookup, or implicit approval.

## Explicit verified HTTPS download (internal)

HTTPS delivery is a separate **host-authorized acquisition step**, not a
`PluginSource` that downloads during runtime discovery. Before downloading,
the host must independently obtain the expected SHA-256 and approve the exact
HTTPS origin. For example:

```python
from invariant.load.acquisition import download_verified_zip

url = "https://plugins.example/voice-a.zip"
expected_sha256 = "0123456789abcdef" * 4  # Replace with a trusted real digest

with download_verified_zip(
    url,
    expected_sha256=expected_sha256,
    allowed_origins={"https://plugins.example"},
) as archive:
    store.install_zip(archive, expected_sha256=expected_sha256)
```

The HTTPS helper streams no more than 30 MiB by default, enforces certificate
validation, refuses redirects and ambient proxy routing, verifies exact bytes,
and removes its temporary ZIP on exit or error. It does **not** resolve plugin
dependencies, grant capabilities, install plugins implicitly, or execute code.
The existing Store validates the archive again and reuses installed plugin
dependencies. The allowed origin controls the destination, not what happens
inside a downloaded plugin; a SHA-256 does not itself establish publisher
trust. A URL obtained from an untrusted catalog cannot also serve as the
trusted source of the expected digest.

## Skip redundant HTTPS downloads (internal)

When a host already has an independently trusted archive SHA-256 and exact
plugin reference, use the explicit installation helper to **reuse** an intact
installation without issuing a second HTTP request:

```python
from invariant.load.acquisition import install_verified_https_plugin

result = install_verified_https_plugin(
    store,
    "example:voice-a@1.0.0",
    "https://plugins.example/voice-a.zip",
    expected_sha256=expected_sha256,
    allowed_origins={"https://plugins.example"},
)
assert "reused" in result  # True when no network transfer was needed
```

The helper first validates URL/origin policy, then under a per-archive lock
checks the installed plugin's exact ID/version, its stored original-archive
SHA-256 and its current file hashes. Identical concurrent requests through
this helper share one download. When the plugin was already installed as
another plugin's dependency, a successful cache hit also registers it as an
installation root without downloading.

If the archive SHA-256 is unknown, differs (including repackaged ZIPs), or
the requested version is absent, a normal HTTPS download and Store install
are required. Modified installed files **fail closed** rather than being
silently replaced; the helper does not repair them. The checked digest is
recorded by this Store, not cryptographic proof against a compromised host
filesystem. No marketplace search or dependency-resolution download is
performed implicitly. A host must explicitly authorize each URL and provide
its trusted digest.

## Pinned Plugin Catalog v1 (internal)

A catalog is only a finite set of artifact offers. It does not fetch packages,
select version ranges, approve installation, or execute plugins. The host must
obtain an **independently trusted SHA-256 of the entire catalog bytes** before
using it (a digest supplied by the same untrusted catalog server is not enough).

```python
from pathlib import Path
from invariant.load.plugin_catalog import VerifiedPluginCatalog

catalog = VerifiedPluginCatalog.from_bytes(
    Path("catalog.json").read_bytes(),
    expected_sha256="<independently-trusted-catalog-sha256>",
)
planner = catalog.planner(store, allowed_origins={"https://plugins.example"})
plan = planner.plan(["example:voice-a@1.0.0"])

# The application presents the exact plan for authorization. Only after
# explicit host approval:
result = planner.apply(plan, approved_plan_id=plan.plan_id)
```

The catalog is verified *offline*, contains only pinned plugin refs, URLs,
archive SHA-256 values and required exact dependencies, and is immutable after
parsing. Its offers still pass the existing planner's HTTPS origin checks; the
downloaded manifests must still match the approved dependency graph. A catalog
digest authenticates bytes only relative to the host's trusted expected digest.
See [Plugin Catalog v1](../reference/plugin-catalog-v1.md) for the schema and
trust boundary.

For a publisher/channel with consecutive catalog editions, use
[Trusted catalog updates](../reference/plugin-catalog-updates.md) to admit
independently pinned update metadata and persist anti-rollback state before
feeding its `VerifiedPluginCatalog` to the same planner.

## Host-approved installation plan (internal)

A host can preview a **finite set of exact plugin versions** before requesting
approval. Offers must come from host-trusted metadata, not arbitrary
unverified marketplace listings. The planner does not fetch catalogs or solve
version ranges; every required dependency must have an exact `id@version`.

```python
from invariant.load.installation_plan import ArtifactOffer, PluginInstallationPlanner

offers = [
    ArtifactOffer(
        ref="example:audio-core@1.0.0",
        url="https://plugins.example/audio-core.zip",
        sha256="<trusted-64-digit-SHA256>",
    ),
    ArtifactOffer(
        ref="example:voice-a@1.0.0",
        url="https://plugins.example/voice-a.zip",
        sha256="<another-trusted-SHA256>",
        dependencies=("example:audio-core@1.0.0",),
    ),
]
planner = PluginInstallationPlanner(
    store, offers, allowed_origins={"https://plugins.example"},
)
plan = planner.plan(["example:voice-a@1.0.0"])
print(plan.installed)   # Intact local dependencies
print(plan.downloads)   # ZIP offers needing explicit acquisition

# The application obtains actual human/host authorization for this exact
# plan_id before invoking apply(). Replace SHA-256 placeholders above.
result = planner.apply(plan, approved_plan_id=plan.plan_id)
```

`plan()` is read-only with respect to plugin installation and makes no
network requests. The host **must** present the plan and obtain approval:
passing `plan_id` programmatically is not human consent. The plan digest
binds chosen roots, artifact URLs/digests, required dependencies, origin
policy, and the current Store catalog snapshot. If the catalog changes,
approval must be obtained again.

The planner downloads all missing ZIPs into temporary storage, validates each
archive's real identity and required dependencies against the approved offers,
then calls **one** `LocalPluginStore.install()` for the full graph. The Store
guards its catalog transaction against concurrent changes. A failed download
does not publish a partial install; failed Store publication may leave
unreferenced object files for guarded manual cleanup. Optional plugin
dependencies and host-granted capabilities are outside this installation graph.
This is an internal, bounded exact-version planner—not a marketplace,
untrusted-plugin sandbox, or automatic dependency resolver.

## Session lifetime and removal

Use `with store.open_service()` for the entire lifetime of the objects you obtain from it. The session holds an on-disk consumer lease and shuts managed instances down before releasing that lease. Do not use returned plugin objects after the `with` block or leave execution-scoped contexts running beyond it.

`uninstall_root("example:voice-a@1.0.0")` removes an installation root from the catalog; it does **not** immediately delete its files or shared dependencies needed elsewhere. Physical pruning is manual and guarded:

```python
# Only after independently verifying no untracked consumers remain.
store.prune_unreferenced(confirmed_untracked_consumers_stopped=True)
```

Never pass that confirmation while any host might still be using bare `plugin_roots()` paths without a lease. Failed shutdown leaves a durable lease for operator recovery. No automatic lease expiry or GC runs in this alpha.

## Trust boundary and future sources

A local ZIP is still executable Python code. **The Store is not a sandbox for malicious plugins.** Use only trusted archives; SHA-256 verifies exact bytes when an independently trusted digest is supplied, but it does not establish trust by itself. Read [Security](../security.md).

The runtime uses an internal `InstalledSource` to discover and load **installed objects** through `PluginLoader` when a host enters `store.open_service()`. The original ZIP may be removed after installation; the source works from a lease-pinned snapshot. Installation and execution are distinct: `InstalledSource.discover()` and `load()` never extract or fetch an archive. The separate `ZipSource` remains a stub for direct ZIP-path loading, and `RemoteSource` remains incomplete. Explicit verified HTTPS acquisition is supported as described above. Direct URL discovery through `RemoteSource`, marketplace lookup, and automatic dependency downloads are not implemented. Future acquisition sources should still deliver verified local artifacts to this same Store after the host has explicitly authorized installation.

## Next

- [Runtime model](../concepts/runtime-model.md) — plugin instances and lifecycle
- [Dependencies and capabilities](dependencies-and-capabilities.md) — runtime dependency declarations
- [Manifest v1](../reference/manifest-v1.md) — exact archive manifest fields
