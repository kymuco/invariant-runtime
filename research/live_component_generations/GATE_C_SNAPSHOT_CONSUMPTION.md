# Gate C.3 — staged, bounded snapshot actually consumed by plugin

**State: RESEARCH PASS — user reported Gate C.3 scoped Windows tests green on 2026-10-14.**
`main`, `invariant/` and public `PluginService` contracts unchanged.

## Previous confirmed results

- Gate C.2: 104 scoped tests reported green by user on Windows (2026-10-14).
- Gate C.1: 96 scoped tests reported green, native GPU managed invocation PASS,
  32 MiB per worker, epoch 2, PIDs 14272 -> 8496, VRAM free 6878 -> 6878 MiB.

## The actual missing proof

C.2 rehashes declared model files before stop and before candidate start.
However, a plugin that subsequently opens the ORIGINAL mutable model path
can still load modified bytes. This is a time-of-check/time-of-use gap.

Gate C.3 demonstrates a minimal COOPERATING-PLUGIN pathway, using only
existing Invariant mechanisms, without another routing authority:

1. `model_asset_paths(root_key)` must declare the source weight file.
2. Opt-in `snapshot_config_prefix=` also requires a real manifest
   `runtime.config_prefix` with that exact prefix and
   `runtime.config_param='config'`.
3. C.2 hashes the source. During metadata-only stage C.3 copies ONE
   verified file into a private temporary vault (NO hardlinks). It checks
   the copied digest and original again. A 16 MiB/default file limit
   prevents accidentally copying large models; maximum accepted is 64 MiB.
   A separate four-snapshot cap also limits cumulative abandoned stages.
4. Before draining the running plugin, validate original C.2 seal AND
   staged snapshot digest. If either differs, keep the old route ACTIVE.
5. After old worker exits and host proves resource release, validate
   original and snapshot again. Use the existing pinned EphemeralPolicy
   config-source path to pass `config['model_path']` to the plugin's real
   `initialize(config)` — the plugin must READ FROM THIS PATH.
6. Both v1 and v2 use separate snapshot copies. The single existing
   StopFirstSlot still owns routing, drain, rollback and resource release.
   Staged copies are disposed on successfully closed exclusive host.

## Usage (tiny test fixture, NOT a real 8GB GGUF)

```python
with ManagedCallPort.exclusive(
    plugin_roots, root_ref='research:tts',
    is_resource_free=trusted_host_probe,
    model_asset_paths=lambda root_key: (weights_by_version[root_key],),
    snapshot_config_prefix='research.asset.',
) as port:
    print(port.invoke('before'))
    print(port.upgrade())
    print(port.invoke('after'))
```

Selected manifest:

```json
{
  "runtime": {
    "config_prefix": "research.asset.",
    "config_param": "config"
  }
}
```

Cooperating plugin:

```python
def initialize(self, config):
    self.weights = Path(config['model_path']).read_bytes()
```

## Test plan

`test_gate_c_snapshot_consumption.py` contains **9** new CPU-only,
actual manifest/ExecutionContext integration cases:

1. ManagedCallPort receives correct v1 and v2 outputs from snapshots.
2. Plugin mutates ORIGINAL weight file inside initialize(), after
   pre-start verification; real plugin reads preserved snapshot bytes.
3. Tampering the snapshot before upgrade fails while v1 remains active.
4. Candidate missing explicit runtime config contract fails before drain.
5. Candidate exceeds explicit copy budget -> reject before v1 shutdown.
6. Candidate source mutated after stage -> C.2 rejects before drain.
7. Multi-file/sharded model cannot silently pass as a complete copy.
8. Repeated abandoned stage requests hit a total four-snapshot budget.
9. Terminal close removes staged temporary snapshot files.

Run the same CPU-only scoped tests through compact `pytest -q`
(no native GPU run needed for this gate):

```powershell
Set-Location W:\dev\invariant-runtime
git pull --ff-only origin research/catalog-snapshot-gate-a-2026-10-11
$python = '.\.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { $python = 'python' }

$suite = @(
    'test_gate_c_snapshot_consumption.py',
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
) | ForEach-Object { Join-Path 'research/live_component_generations' $_ }

& $python -m pytest -q @suite
if ($LASTEXITCODE -ne 0) { throw 'GATE_C3_SCOPED_FAILED' }
Write-Host 'GATE_C3_SCOPED_OK'
```

Expected **113** methods (9 C.3 + 104 previous). The user previously
reported all scoped tests green using the older verbose unittest
commands; this pytest invocation is now preferred but has not yet been
independently executed on the Windows host. This remains research PASS,
not production certification.

## IMPORTANT — explicit non-goals and limits

- This is a **tiny single-file research proof**, NOT a production store
  for 8 GB model weights or a replacement for content-addressed artifacts.
- Read-only file attribute + private temp path is NOT an adversarial
  immutable file guarantee. A writer with filesystem permissions can
  modify that snapshot during loading. Multi-shard consistency, external
  artifact bytes and transitive package files are not covered.
- A plugin that ignores the injected path still loads the mutable source;
  one manifest flag is not a sandbox or a proof of compliance. The real
  plugin fixture verifies use of the host-provided path, but arbitrary
  third-party plugins require a stronger loader/worker contract.
- Snapshot copies are staged on disk while the old model is live.
  The copy has I/O/storage cost; never silently duplicate large weights.
- No crypto guarantee for malicious disk writes after final verification,
  and no guarantee that a RAM-loaded model is transferable across process
  termination. WDDM GPU release is an independent resource proof.
- For the actual HDE large-model integration, prefer versioned immutable
  files or a dedicated content-addressed store with read-only model access
  and atomic multi-file publication. This experiment defines what to
  enforce, not a production implementation of that store.


## Additional Windows pytest confirmation (2026-10-14)

User additionally ran the corrected active-environment invocation through
`Get-Command python` rather than assuming `.venv/Scripts/python.exe` exists.
The three focused Gate C test files (C.3 snapshot consumption, C.2 model
assets and C.1 managed call) were reported **all green** under `pytest -q`.
Expected focused count: 9 + 8 + 10 = **27** methods. The user reported
green status, not a copied pytest summary line with counts. Previous broader
113-method Windows scoped regression was separately reported green using
verbose unittest; this new run confirms the focused pytest workflow.

Future Windows commands should resolve the ACTIVE Python interpreter:

```powershell
$python = (Get-Command python -ErrorAction Stop).Source
& $python -m pytest -q research/live_component_generations/test_gate_c_snapshot_consumption.py research/live_component_generations/test_gate_c_model_assets.py research/live_component_generations/test_gate_c_managed_call.py
if ($LASTEXITCODE -ne 0) { throw 'GATE_C3_PYTEST_FAILED' }
```

Do not interpret a stale `$LASTEXITCODE` after a PowerShell command-not-found
failure as a test result. No production GPU/LLM integration is implied.
