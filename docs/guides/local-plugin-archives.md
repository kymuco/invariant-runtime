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

The `ZipSource` and `RemoteSource` discovery backends remain incomplete. This guide does **not** imply that discovering a ZIP activates an installer or that URLs/marketplaces are implemented. Future acquisition sources should deliver verified local artifacts to the same Store, with any download or installation explicitly authorized by the host.

## Next

- [Runtime model](../concepts/runtime-model.md) — plugin instances and lifecycle
- [Dependencies and capabilities](dependencies-and-capabilities.md) — runtime dependency declarations
- [Manifest v1](../reference/manifest-v1.md) — exact archive manifest fields
