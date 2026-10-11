# Gate A.2 — activation-consumption seam audit

**Status:** research-only; no production or public API changes.
**User Windows result (2026-10-11):** 40 scoped tests reported green on the
previous Gate A research branch head `5139fc7640cd65cabb64cd8b93623d6607a35152`.
This is user-reported evidence, not an assistant-local or CI run. It covers
16 catalog epoch tests, 14 genuine existing `PluginService` refresh tests,
and 10 new pinned-view tests. It is not a complete core/GPU/Windows qualification.

## Source-level ownership map (current production implementation)

| Boundary | Production path | Consequence |
| --- | --- | --- |
| Initial manifest discovery | `invariant/api/service.py:from_manifest_paths` | One registry constructed, descriptors initially registered |
| Version selector | `invariant/catalog/registry.py:resolve` | Unpinned/`latest` selects current highest version |
| Dependency closure | `invariant/exec/runtime/resolver.py:DependencyResolver.collect` | Repeated `registry.resolve` calls, no pinned epoch argument |
| Top-level activation | `invariant/exec/runtime/execution_runtime.py:ExecutionRuntime.get` | Resolves root key, collects graph, then walks order |
| Instance construction | `execution_runtime.py:_create_and_init` | Re-resolves descriptor and raw dependency specs |
| Transaction injection | `invariant/exec/runtime/activation.py:ActivationTransaction.dependency_get` | Calls `_canonical_key(dep_ref)` anew, may select newer `latest` |
| Cross-scope injection | `invariant/exec/runtime/execution_context.py:_dependency_get` | Routes dependencies into another runtime; no catalog-view pin |
| Managed instance cache | `invariant/catalog/registry.py:_instances` | Shared lifetime and raw object references not leased |

The **critical missed boundary** is between planning `execution_order()` and
actual `ActivationTransaction.dependency_get()`: a pinned-looking graph is
not enough when the raw manifest dependency is resolved again later. A new
version appearing there can alter the injected dependency even if the
activation graph had selected an older version.

## Concrete negative characterization

`test_gate_a_activation_resolution_gap.py` is a new **unrun** test that
inserts a new real manifest/descriptor *after* `DependencyResolver.collect()`
and *before* the activation loop. The current implementation is expected to:

1. Collect a root graph with `research:lex@1.0.0`;
2. Register `research:lex@2.0.0` in the mutable service registry;
3. Initialize the TTS root with lexicon **v2**, despite graph **v1**.

A green result of this characterization test proves the **existing
inconsistency**. It does not prove a solution. The test's intentional mutable
registry injection is strictly local to a temporary service under
`research/`, not an endorsed plugin update path.

Run it separately to avoid conflating with the 40 previously green tests:

```powershell
Set-Location W:\dev\invariant-runtime
git fetch origin
git switch research/catalog-snapshot-gate-a-2026-10-11
git pull --ff-only origin research/catalog-snapshot-gate-a-2026-10-11
$python = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }
& $python -m unittest discover -s research/live_component_generations -p "test_gate_a_activation_resolution_gap.py" -v
if ($LASTEXITCODE -ne 0) { throw "GATE_A_ACTIVATION_GAP_PROOF_FAILED" }
Write-Host "GATE_A_ACTIVATION_GAP_REPRODUCED"
```

If it fails unexpectedly, first inspect the traceback; do not weaken the
assertion or claim the bug is absent without tracing the actual versions.

## Minimal coherent future contract (proposal, not approved API)

1. **One authoritative catalog source.** The production registry/service
   must expose immutable descriptor snapshots through a **single published
   owner**. A sidecar `CatalogViews` plus an independent mutable registry
   must not become production's two competing catalogs.
2. **Per-activation view acquisition.** At admission to a managed operation,
   capture the chosen catalog view once; retain it for dependency closure,
   descriptor lookup, activation and all nested dependency retrieval.
3. **Pinned dependency injection.** Resolve unpinned manifest specs into
   exact `id@version` in the retained activation graph, and use those exact
   refs in both staged and cross-policy injection. Never independently
   resolve raw `latest` after the plan is formed.
4. **Preserve policy boundaries.** A graph-wide exact version selection
   must not bypass `EXECUTION`, `TRANSIENT`, `SINGLETON`,
   host capability permissions, or explicit per-dependency policy overrides.
   If the first mode cannot pin mixed scopes coherently, reject them
   explicitly rather than promising a generic hot-swap guarantee.
5. **Separate activation from lifecycle.** Immutable graph planning does not
   stop old instances or grant usage authority. Tracked generation leases
   and resource policy belong to Gate B and host adapters.
6. **Raw reference limitation.** Existing `PluginService.get()` returns
   a raw managed object. No automatic safe retirement or lease semantics
   can be claimed for that reference without an opt-in ownership boundary.

## Promotion decision

**Gate A metadata proof:** PASS (user-Windows, 40 scoped methods).

**Gate A production activation coherence:** OPEN / NOT PROVEN.
The new deterministic negative characterization is authored, not executed.
Do not modify `invariant/` or open a production hot-reload API until the
actual version-drift boundary is reproduced and a coherent single-view
activation transaction is demonstrated in research.

**Gate B leases:** NOT STARTED for production contracts.
