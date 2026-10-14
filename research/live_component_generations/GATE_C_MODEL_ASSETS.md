# Gate C.2 — model asset integrity before exclusive hot-swap

**Status:** research PASS — 104/104 scoped tests reported green on user Windows (2026-10-14).
All changes are under research/live_component_generations/. No main, public
PluginService, production loader or kernel edits.

## Prior confirmed evidence

- Gate C.1: user reported scoped checks all green (96 methods expected) and
  native GPU E2E PASS, exit code 0, on October 13, 2026.
- C.1 managed invocation: epoch 2, v1 1.0.0:first, v2 2.0.0:second,
  worker PIDs 14272 then 8496.
- RTX 5070 total CUDA 12226 MiB; 32 MiB allocations per child; v1 observed
  global free-memory drop 172 MiB; baseline free 6878 MiB; end free 6878 MiB.
- Exact same observed free-memory baseline and final does not establish
  per-PID accounting or reliability of LLM checkpoints.

## Why C.2

Gate A pinned manifest metadata and the main plugin.py. Real LLM/TTS models
may load additional weight shards, tokenizers and runtime config files.
Without an explicit asset seal, those extra bytes can change after the
replacement is staged, so a metadata-only upgrade may stop a healthy old
model and then start an unexpected candidate.

## Narrow change — no new scheduler

`gate_c_asset_fence.py` (stdlib SHA256):

- A trusted host explicitly supplies the complete list of file paths
  needed by the selected root version (`model_asset_paths(root_key)`).
- 1..128 declared regular files per selected root, no direct symlinks or
  duplicate paths; read each in a 4 MiB streaming buffer.
- Save immutable (resolved path, size, SHA256) tuples inside the existing
  `ExclusiveStage`, associated with its exact root version.
- Before **any stop-first admission drain or v1 shutdown**, independently
  hash the current declared files and reject changed bytes/paths as StalePlan.
- Verify again **immediately before opening** the successor's
  ExecutionContext (after old resource was released). If this late
  check fails, the already tested stop-first rollback restores a fresh
  original version only if the resource-free predicate permits it.

`gate_b_exclusive_host.py` has a backwards-compatible optional
`model_asset_paths=` parameter. `gate_c_managed_call.py` forwards it on
`ManagedCallPort.exclusive(...)`. All previous callers can omit it.
The port itself still holds no second route pointer, registry or resource
scheduler. The path locator is a trusted host configuration, not plugin
self-declared authority.

## Example

```python
def assets(root_key: str):
    return {
        'my:llm@1.0.0': [Path('D:/AI/Models/LLM/v1/model.gguf')],
        'my:llm@2.0.0': [Path('D:/AI/Models/LLM/v2/model.gguf')],
    }[root_key]

with ManagedCallPort.exclusive(
    roots, root_ref='my:llm',
    is_resource_free=host_owned_worker_and_gpu_probe,
    model_asset_paths=assets,
) as port:
    print(port.invoke('test'))
    print(port.upgrade())
```

These paths are examples, not claims that those files actually exist.
An actual model with multiple shards/config/tokenizer files must enumerate
**all** its required assets. The callback pins only what it declares.

## Gate C.2 CPU-only test plan

`test_gate_c_model_assets.py` contains **8** tests with real Invariant
manifest-backed plugin instances and tiny synthetic model.safetensors files:

1. Valid weight seal -> managed invocation upgrade -> v2 model content.
2. Mutated weight bytes before replace -> StalePlan, v1 still READY.
3. Missing candidate weight -> abort before v1 drain.
4. Added entry in trusted asset declaration -> abort before shutdown.
5. Mutation after resource release, before candidate start -> catch,
   restore fresh v1, retain epoch 1.
6. Direct symlink asset rejected if OS permits symlink creation.
7. Missing/duplicate trusted declarations rejected.
8. Equal-length weight mutation with reset mtime still rejected by SHA256.

Windows test command:

```powershell
Set-Location W:\dev\invariant-runtime
git pull --ff-only origin research/catalog-snapshot-gate-a-2026-10-11
$python = '.\.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { $python = 'python' }

foreach ($file in @(
    'test_gate_c_model_assets.py',
    'test_gate_c_managed_call.py',
    'test_gate_b_cuda_evidence.py',
    'test_gate_b_exclusive_host.py',
    'test_stop_first.py',
    'test_gate_b_leased_host.py',
    'test_gate_a_readiness_host.py',
    'test_gate_a_catalog_owned_host.py',
    'test_gate_a_pinned_execution.py',
    'test_gate_a_catalog_view.py'
)) {
    & $python -m unittest discover -s research/live_component_generations -p $file -v
    if ($LASTEXITCODE -ne 0) { throw "GATE_C2_FAILED: $file" }
}
Write-Host 'GATE_C2_SCOPED_OK'
```

**Expected if all pass:** 8 new + 96 previous = 104 scoped tests.
This is CPU-only and does not rerun native C.1 CUDA E2E, which was already
user-verified at the previous commit.

## Strict limitation

This is *detection*, NOT immutable content-addressed file storage.
A mutation can still occur after the final hash and before/during actual
plugin loading: TOCTOU is not eliminated. The direct model-file path is
not redirected to an immutable copy. There is no locking of a 8+ GB model
file, no atomic multi-shard snapshot, no full graph dependency-asset
inventory and no cryptographic attestation of dependencies outside the
trusted host's explicit declared list. Direct symlinks are forbidden but
symlinked ancestor directories and multi-host adversarial mutations are
not a complete security boundary. Multi-GB hashing is I/O heavy, not
zero-cost. A robust production design should use immutable/versioned
content-addressed artifacts or explicit model-loader file-handle pinning.

Do NOT treat a C.2 PASS as a general hot-swap/LLM production GO.


## Windows completion evidence (2026-10-14)

User reported all scoped C.2 checks green. The suite expected 104 methods:
8 new SHA256 model-asset tests and 96 prior Gate C.1 / B / A methods.
This closes Gate C.2 as research PASS, not immutable model loading or
production approval. Gate C.3 separately tests the real plugin consuming
a pre-copied, bounded snapshot via existing Invariant config injection.
