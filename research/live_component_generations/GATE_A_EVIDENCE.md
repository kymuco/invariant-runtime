# Gate A — catalog snapshots (research evidence, not production approval)

Branch: `research/catalog-snapshot-gate-a-2026-10-11`
Base: `097b492aba213128bbe8aaf9defa858da6eec046`
Scope: research-only; no `invariant/` or public facade changes.

## Why this fork exists

The older `LiveServiceCatalog.publish()` publishes into
`PluginRegistry._descriptors` first and then updates a separate
`CatalogEpochs._current`/epoch. If an error occurs after the registry write,
the two views can disagree. Its real `DependencyResolver` also reads the
mutable registry repeatedly while walking a graph.

A lock for one lookup is not a graph-wide epoch guarantee. The old
same-`PluginService` refresh pilot remains an **unqualified experiment**.

## Narrow Gate A candidate

`gate_a_catalog_view.py` isolates a different, deliberately smaller contract:

- Construct a complete descriptor snapshot through real Invariant
  `PluginRegistry` and `DependencyResolver` objects, but in a **detached**
  registry that is never published into the running `PluginService`.
- Validate every full dependency graph before exposing the candidate.
- Retain a view with its own exact descriptor/version set. Even a `latest`
  root selector is evaluated against that retained view, not against a
  concurrently modified global registry.
- Compare staged artifact signatures against fresh discovery.
- Publish a new read-only view with **one** coordinator pointer assignment
  guarded by a base-view CAS.
- Existing captured views continue to resolve the original exact graph.

This is a metadata consistency experiment, not a usable in-place refresh
feature. There is no new public API, plugin activation, lifecycle binding,
managed lease, resource tracking, or hot swap.

`CatalogView` is read-only through its intended facade, **not a Python
security boundary**. Caller-provided Candidate fingerprints are only as
complete as their discovery implementation; the existing pilot hashes the
manifest and primary Python file, not transitive imports or model weights.
The descriptors themselves must be treated as staged immutable inputs.

## Test provenance

At authoring time:

- Prior `test_catalog_epoch.py`: 16 test methods, earlier local results
  recorded in the seven-pilot audit (not a fresh run here).
- Prior `test_live_service_catalog.py`: 14 genuine `PluginService`
  integration test methods **written but not independently verified**.
- New `test_gate_a_catalog_view.py`: 10 new genuine descriptor/resolver
  integration methods **authored but not yet run on the branch**.
- Only Python 3.13.5 syntax compilation of the originally authored two
  new Python files was performed in an assistant-local scratch copy.
  That does **not** certify the pushed branch nor its integrations.
- No GitHub Actions, Windows/VRAM gates, complete core suite, or
  production regression run has been performed.

Run from the exact checkout, using the repo Python environment:

```powershell
Set-Location W:\dev\invariant-runtime
git fetch origin
git switch research/catalog-snapshot-gate-a-2026-10-11
git pull --ff-only origin research/catalog-snapshot-gate-a-2026-10-11

$python = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }

foreach ($file in @(
    "test_catalog_epoch.py",
    "test_live_service_catalog.py",
    "test_gate_a_catalog_view.py"
)) {
    & $python -m unittest discover -s research/live_component_generations -p $file -v
    if ($LASTEXITCODE -ne 0) { throw "Gate A FAILED: $file" }
}
```

A green run would prove the 40 scoped methods under that interpreter,
**not** all of Invariant's runtime, shared `SINGLETON`/mixed scopes,
artifact trust or in-place service refresh.

## Go/no-go

**GO for further Gate A research** only if scoped real-object tests pass,
a separately pinned graph stays consistent while the current-view pointer
moves, and the old-view lifetime remains explicit.

**NO-GO for promotion to `invariant/`** until a production owner
chooses one authoritative descriptor/read path, maps the current
`PluginService` and execution/instance ownership boundary, adds
failure-injection and concurrency regression coverage, and runs the full
core suite on supported Python versions. Do not port the research private
registry mutation as an API.

After Gate A, Gate B would separately prove managed operation leases,
exact generation ownership, and cleanup under concurrent switch. GPU,
worker retry policy, permission and host resource strategy remain outside
the initial core contract.
