# Gate C.4 — pinned no-copy model bundles (Windows research)

**Status: research PASS — user reported all 41 focused C.1–C.4 pytest checks green on Windows (2026-10-14).** No production or main
changes. This is an opt-in extension of the existing C.1
`ManagedCallPort → RealStopFirstHost → ExecutionContext` route, not a
second router, scheduler, worker supervisor or model cache.

## Previously confirmed

- Gate C.1: real managed CUDA E2E PASS (RTX 5070; v1/v2 PIDs distinct;
  GPU free-memory 6878 MiB before and after).
- Gate C.2: scoped 104 tests green on user's Windows.
- Gate C.3: 113 scoped tests green; additional focused 27 tests green
  under `pytest -q` using active Python environment.

## Threat model and design

- Host declares every relevant weight/config/tokenizer file with
  `model_asset_paths(root_key)`, **all in one fixed model directory**.
- Separately, the **trusted host** supplies immutable/version-controlled
  filename→lowercase SHA-256 pins through `trusted_model_sha256(root_key)`.
  These trusted pins must originate OUTSIDE writable model files.
  A plugin's own manifest or a hash freshly computed from untrusted
  disk bytes is not a trusted expected value.
- C.2 hashes the whole declared bundle in bounded memory (4-MiB chunks).
  C.4 compares exact filenames and hashes with trusted pins, then opens
  every file with Windows `CreateFileW(GENERIC_READ, FILE_SHARE_READ)`
  — intentionally **without FILE_SHARE_WRITE or FILE_SHARE_DELETE**.
  If any file has a conflicting open writer, fail before stopping v1.
  After acquiring all handles, C.2 re-verifies contents.
- OS handles are retained by the SAME exclusive host for every staged
  generation and for the active plugin's lifetime. `stage()` may pin
  bytes and file handles but must NEVER initialize the candidate GPU worker.
  The existing StopFirstSlot handles admission/drain/physical resource
  proof. On successful replacement, retire old file handles only after
  the old generation exits. On terminal successful EMPTY, release all.
  If resource release is uncertain and FAILED, do not equate a
  close return with ownership cleanup.
- The selected manifest explicitly declares `runtime.config_prefix`
  and `runtime.config_param='config'`. The pinned ExecutionContext's
  existing EphemeralPolicy config path injects string values:
  `model_root`, `model_paths_json`, `model_sha256_json`, `root_key`.
  The **cooperating plugin** must use the injected paths instead of any
  independently chosen source location.
- Files are NOT copied, hard-linked, moved or renamed by this experiment.
  It uses open OS handles + bounded hashing buffers and no GPU
  allocations of its own. No artificial 16-MiB C.3 copy limit;
  model file size is bounded by disk/OS and read time, not an in-memory copy.
- At most 128 declared files per root, and at most five retained
  staged/active bundles (each up to 128 handles) in this narrow research
  prototype. Multi-directory and missing-file sets fail closed.

## Usage

```python
# The host owns these reference digests, independently of the model files.
trusted = {
    "example:llm@1.0.0": {
        "shard-1.gguf": "<64-character trusted SHA256>",
        "shard-2.gguf": "<64-character trusted SHA256>",
    },
    "example:llm@2.0.0": {
        "shard-1.gguf": "<64-character trusted SHA256>",
        "shard-2.gguf": "<64-character trusted SHA256>",
    },
}

with ManagedCallPort.exclusive(
    plugin_roots,
    root_ref="example:llm",
    is_resource_free=host_owned_worker_reap_and_gpu_probe,
    model_asset_paths=lambda root_key: installed_files[root_key],
    trusted_model_sha256=lambda root_key: trusted[root_key],
    locked_model_config_prefix="research.model.",
) as port:
    print(port.invoke("before"))
    epoch = port.upgrade()
    print(port.invoke("after"))
```

The selected plugin's manifest contains
`{"runtime":{"config_prefix":"research.model.","config_param":"config"}}`.
In `initialize(config)`, the plugin uses
`json.loads(config["model_paths_json"])` and reads those exact files;
`model_sha256_json` and `root_key` are also supplied. These are
demonstrative contracts, not a current public PluginService API.

## Native Windows CPU gate

14 new tests in `test_gate_c_locked_assets.py`, of which 12 require
Windows native file-sharing support (the remainder confirms failure on
unsupported OS). The tests use REAL manifest-backed Invariant plugins
but small synthetic model shards, not a downloaded LLM.

```powershell
Set-Location W:\dev\invariant-runtime
git pull --ff-only origin research/catalog-snapshot-gate-a-2026-10-11

$python = (Get-Command python -ErrorAction Stop).Source
& $python -m pytest -q `
    research/live_component_generations/test_gate_c_locked_assets.py `
    research/live_component_generations/test_gate_c_snapshot_consumption.py `
    research/live_component_generations/test_gate_c_model_assets.py `
    research/live_component_generations/test_gate_c_managed_call.py

if ($LASTEXITCODE -ne 0) { throw "GATE_C4_PYTEST_FAILED" }
Write-Host "GATE_C4_SCOPED_OK"
```

Expected focused results: **41 tests** (14 C.4 + 9 C.3 + 8 C.2 +
10 C.1). A Windows-skip in a native lock test is NOT evidence of
Windows share-mode behavior. Do not claim native C.4 PASS without
actual Windows tests and exit status 0.

## Hard limits

The Windows `FILE_SHARE_READ` sharing agreement rejects *new* write/delete
opens while held, but it is NOT an absolute immutability primitive.
Writes via previously opened compatible handles, existing writable
mappings, raw devices/privileged actors, directory-ancestor rename,
filesystem reparse behavior, model loaders that disregard injected paths
and other adversarial activity are NOT certified. Locks are per filename
object/share protocol and are not a full storage integrity authority.
Hash verification during active plugin use is not performed per read.

The trusted SHA-256 callback itself must be strongly versioned/read-only
in the host's configuration; this code does not verify signed supply-chain
metadata. There is no multi-directory atomic snapshot and no proof that
multi-GiB models can load or hot-switch within actual available VRAM.

On non-Windows, the C.4 mode fails closed. The existing C.1/C.2/C.3
mechanisms remain opt-in and unchanged when C.4 is omitted.

**Promotion decision: NO-GO** until native Windows test evidence,
resource supervision under failed/crashed workers, verified trusted pin
provenance and real model-loader cooperation are established.


Additional negative proof: after a failed resource-release predicate the
slot must remain FAILED, and the model file leases must remain held for
host-controlled manual recovery rather than being freed when the stop
attempt returns. The last test exercises this explicitly.


## Native Windows completion — 2026-10-14

The user reports the entire Gate C.4 focused pytest run green on Windows:
14 new C.4 checks and 27 C.1–C.3 scoped regression checks, **41 total**.
This is user-reported Windows execution evidence, not a GitHub Actions or
assistant-local run. The tests validate Win32 CreateFileW read-sharing,
write/delete denial, multi-file real Invariant plugin invocation and
replacement, trusted per-version SHA-256, no-copy staging, staged-handle
resource bounds, and retaining file handles on failed release evidence.

**Research Gate C.4 = PASS. Production readiness = NO-GO.** A real
model-loader, real large/multi-shard weights, process/OS crash recovery,
adversarial concurrent writers and GPU allocation behavior are not yet
established by this CPU-only suite. Next distinct work item is a model-loader
E2E that consumes the already-verified pinned paths without adding another
scheduler or a second catalog authority.
