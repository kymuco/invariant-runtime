# Invariant Runtime: audit of seven live-component research pilots

**Status:** research decision record, 2026-10-11. **Not an approved public
API, implementation plan, or instruction to merge.** Scope is this
`research/live-component-generations-v0-2026-10-11` branch only. Audited
`main` baseline: `68ff60e04cdfbac3f43a8b80901b15fc74e6d8d2`.

## Decision in one paragraph

**GO** for a *small, independently proved core primitive* for immutable catalog
snapshots, exact dependency binding and scoped runtime-owned instance leases.
**NO-GO** for copying seven research implementations into `invariant/` or
exposing broad `reload()/update()/resource_manager()` public APIs now. The
experiments establish useful behaviors, but do not yet prove that a shared
registry, dependency resolver, managed instances and worker lifecycle are
**one coherent transaction under concurrency**. Keep host intent, permission,
provider selection, resource policy and GPU scheduling out of the core.

Invariant's intended boundary, per [AGENTS.md](../../AGENTS.md), is an
independent Python 3.11+ / stdlib-only plugin runtime. It is not HDE, an
agent, a marketplace, an approval system or a full operating-system scheduler.

## Audited sources and evidence

| Pilot | Relevant files | Evidence and what may be reused |
| --- | --- | --- |
| 1. Parallel generation binding | [prototype.py](prototype.py), [test_prototype.py](test_prototype.py) | 10 previous reported tests; scoped lease, atomic binding for new managed calls, retire-on-final-release. Does **not** capture raw references returned by `PluginService.get()`. |
| 2. Exclusive stop-first | [stop_first.py](stop_first.py), [test_stop_first.py](test_stop_first.py) | 15 previous reported tests; stop admission, drain, resource-release gate, best-effort restore and fail-closed. Synchronous arbitrary callbacks **cannot** promise bounded shutdown. |
| 3. Real subprocess worker | [worker_supervisor.py](worker_supervisor.py), [test_worker_process.py](test_worker_process.py) | 12 previous reported Linux tests; actual process reaping and exclusive TCP port as a resource stand-in. No real GPU or Windows process-tree proof. |
| 4. Exit supervision | [supervision.py](supervision.py), [test_supervision.py](test_supervision.py) | 13 previous reported tests; desired vs observed state, bounded restarts and no automatic failed RPC replay. Not integrated with the swap coordinator. |
| 5. Liveness and deadlines | [health_probe.py](health_probe.py), [test_health_probe.py](test_health_probe.py) | 12 previous reported tests; distinguish missed heartbeat from an overdue request; late worker readiness does not itself imply a missed heartbeat. Heartbeat does **not** mean model progress. |
| 6. Public facade integration | [public_facade_bridge.py](public_facade_bridge.py), [test_public_facade_bridge.py](test_public_facade_bridge.py) | 10 integration tests reported green by the Windows host user; creates a new `PluginService` per generation. Establishes a useful compatibility path, **not** in-place refresh. |
| 7. In-place catalog refresh | [catalog_epoch.py](catalog_epoch.py), [live_service_catalog.py](live_service_catalog.py), [test_live_service_catalog.py](test_live_service_catalog.py) | 16 stdlib and seven fake-adapter checks passed in a separate local variant. **14 real-PluginService tests have not been verified here**. Research adapter writes registry private fields; NOT a production contract. |

This branch defines **102 test methods** (62 for pilots 1–5, 10 for pilot
6, 16 + 14 for pilot 7). This is **test inventory, not a claim that one
102-test branch run passed**. During this audit, local copies of the pilot
7 epoch and fake-adapter checks passed 16/16 and 7/7 on Python 3.13.5.
Those local copies are **not byte-identical** to files on this GitHub branch;
therefore those runs do not certify the pushed commit. The latest 14 real
catalog-refresh integration tests remain a hard verification gate. We did not
run the full Invariant core suite or GPU/Windows proof during this audit.

### Existing runtime constraints

- `PluginService.from_manifest_paths()` discovers/constructs the registry
  **once**. No documented public in-place refresh API exists. See
  [service.py](../../invariant/api/service.py) and
  [public API reference](../../docs/reference/public-api.md).
- `PluginRegistry.register()` currently **overwrites** a descriptor for
  the same `id@version`; `clear()` removes descriptors **and** managed
  instances. Neither is a suitable live-refresh transaction. See
  [registry.py](../../invariant/catalog/registry.py).
- The [resolver](../../invariant/exec/runtime/resolver.py) calls `resolve()`
  repeatedly while walking a graph. `latest` is dynamically resolved,
  not guaranteed to remain stable for one activation if the catalog changes.
  A pointer swap protects **one lookup**, not the whole graph.
- `ManifestSource._load_module()` reuses `sys.modules` for an existing
  synthetic name. Replacing bytes at the same path/version does not
  atomically replace existing Python code. New immutable paths or workers
  are preferable to `importlib.reload()`.
- Existing [execution context](../../invariant/exec/runtime/execution_context.py)
  controls ephemeral state; existing managed `get()` hands the caller a raw
  Python reference with no tracked lease. Ownership guarantees must be
  **opt-in and scoped** rather than retroactively asserted for all references.
- Existing host capabilities are **permissions**. A supplied functional
  contract such as `speech.synthesize@1` is **not** a filesystem/network
  permission and must not silently imply one.

## Critical findings (severity ordered)

### P0 — Single authoritative catalog snapshot, not two commits

Pilot 7 uses `CatalogEpochs._current` and
`PluginRegistry._descriptors` as separate stores. The callback can mutate
the latter before the epoch pointer increments. A failed callback after
external mutation leaves **two different apparent catalog epochs**.
This was reproduced locally with a deliberately throwing commit callback:
the epoch remained at 1 while the mock external catalog had advanced to 2.

**Required invariant:** the catalog descriptor index, revision number,
version keys and lookup view must be published by **one authoritative,
immutable snapshot pointer**, and readers must acquire a consistent view.
External callbacks must not perform an irreversible, independently visible
publish before that pointer. Avoid using the current research callback as
the production commit primitive.

### P0 — One dependency resolution uses exactly one catalog epoch

A thread can resolve an unpinned dependency before publication and another
after publication. The resolver currently reads a mutable registry on each
lookup, so a graph may mix epochs even though each individual lookup is
thread-safe. A `latest` selection must resolve into a **concrete exact
version within a frozen view**, and that view must remain pinned until the
activation/dependency transaction ends.

**Required invariant:** `resolve_graph(root, catalog_view)` is logically
pure against that view, and an execution generation retains the resolved
exact-version closure. Don't silently rewrite dependencies of active
contexts when adding a plugin version.

### P0 — Instance ownership and publication are different transactions

Catalog publication must never automatically stop a running instance.
New invocation routing may change only after the candidate is ready and the
host has accepted the change. Old generations retire after the last **tracked**
lease, or after an explicit, documented forced-stop decision. This guarantee
does not cover arbitrary raw plugin references or callbacks retained by
external systems.

One possible narrow invariant: `catalog revision != generation identity !=
live instance identity`. Keep those identities separate.

### P0 — Unproven stop or resource release fails closed

For an exclusive GPU/port/device, stop-first must deny new admissions, await
tracked activity, confirm old termination **and resource release**, and
only then start the new generation. If stop or release is unproven, do not
launch a replacement on the same exclusive resource. A successful software
`shutdown()` callback **alone** is not proof that VRAM was freed.
Recovery of the previous version is best-effort and **does not restore
in-memory/model-stream state or external effects**.

### P1 — Staging does not establish artifact immutability or trust

SHA-256 over manifest and one `plugin.py` detects some changes but ignores
imports, weight files, configs, symlink races and runtime-written assets.
Package acquisition, provenance, signatures and host permission grants must
be solved at the installer/host boundary; a catalog descriptor must reference
a **pinned verified artifact** before strong atomicity claims can be made.
Do not load untrusted Python into the host while merely enumerating metadata.

### P1 — Supervisor failure policy must not leak into the catalog model

Pilot 4 handles process exit; pilot 5 separately implements heartbeats,
deadline RPC and restart limits. They are alternatives/steps toward **one
execution-backend contract**, not two more core managers. Both still lack a
production process tree, durable host-crash recovery and real GPU proof.
RPC timeout is **unknown outcome**, not an instruction to retry user work.
A healthy heartbeat proves liveness of its emitter, not throughput or
correctness of a local LLM.

### P1 — Public facade, policy and compatibility boundaries

Research pilot 6 creates separate `PluginService` instances; pilot 7
mutates `registry._lock` / `registry._descriptors` and assumes access to
`service.loader`. These internals are explicitly **not** supported in
[public API](../../docs/reference/public-api.md). Do not convert the pilot
7 implementation into a production dependency. Preserve policy-gated
capabilities, existing dependency scopes and the exported 0.x contract.
Tests scoped to `EXECUTION` are **not** evidence for `SINGLETON`,
`TRANSIENT`, or mixed-policy graphs.

## Minimal conceptual kernel

These are **semantic contracts, not committed API/class names**:

1. **Immutable CatalogView**: one epoch/revision, a mapping of exact
   plugin keys to verified descriptors/artifact identities, and a resolver
   that can only read that view. A new view can be prepared while an old
   one remains in use. Publish is compare-and-swap against expected epoch
   and is the only visibility point. No partial commit, no same-version
   overwrites, no implicit instance teardown.
2. **ResolvedGraph / ActivationPlan**: retains its CatalogView and exact
   dependency closure, preserving the existing resolver's policy and
   capability semantics. Activation can still fail; it must leave the
   published catalog and other generations unchanged. Graph validation is
   metadata-only when possible; startup is a separate action.
3. **OwnedGeneration + Lease**: an execution-scoped generation owns the
   instances/context that it activates. A stable *host-owned* binding
   points new managed operations to an active generation; acquisition
   pins that generation until release. Retirement removes new admission
   but waits for tracked leases before cleanup. Lifecycle outcomes are
   observable; cleanup errors are not silently converted to success.

**Not first-kernel requirements:** automatic download/install, marketplace,
general functional-contract registry, model-specific adapters, GPU scheduler,
global idle-eviction policy, heartbeat transport, restart strategy matrix,
TTS streaming migration, or agent/consent decisions. A worker executor may
implement readiness/deadlines/termination as an adapter under an existing
execution boundary. Host policy chooses `parallel`, `stop-first` or
`deferred` as operational behavior, not a new family of base classes.

Conceptual view:

```text
Host intent / approval / resource policy (outside core)
           |
           v
 CatalogView(epoch N) --- prepare/CAS publish ---> CatalogView(epoch N+1)
           |                                        |
           +-- pinned graph N                       +-- pinned graph N+1
                  |                                         |
            OwnedGeneration A -- lease/drain -->       OwnedGeneration B
                  |                                         |
                  +------ backend (in-process / worker) ----+
```

The **host stays alive**, but zero downtime is only possible when resources
and migration semantics permit it. For stop-first, a temporary provider outage
is an expected, explicitly observable outcome.

## Proposed evolution gates (NOT an implementation approval)

**Gate A — Catalog identity and snapshot consistency (research only).**
Write failing regression tests for: (a) same-version overwrite prohibited;
(b) old snapshot + new snapshot resolve complete graphs under concurrent
publish; (c) stale CAS plan rejected; (d) throwing pre-publication validation
does not leave the registry and epoch divergent; (e) resource/file content
mutated after staging is rejected before activation. Retest with real
`PluginService`, `PluginRegistry` and `DependencyResolver`.

**Gate B — Managed lease and ownership.** Make the managed operation boundary
explicit, preserve old execution state through final borrow, handle concurrent
switch/deactivate, expose stop/cleanup failures, and prove **no implicit
switch** of existing raw `service.get()` references. Include mixed
dependency scopes or explicitly reject them in a documented initial mode.

**Gate C — Exclusive backend release.** On Windows, validate worker exit and
resource reclamation under real host execution, bounded stop escalation and
failure fencing. Separately test local TTS/LLM at model boundaries; a TCP port
test does not establish physical GPU release.

**Gate D — Contract promotion review.** Only after A and B pass on supported
Python versions (3.11+) and focused/full core regression suites, decide
whether to expose a minimal host-facing method. First write contract tests
and normative docs. Keep source changes small; do not add all speculative
managers at once.

## Unresolved product decisions (belong to host or explicit later contracts)

- What should new calls do while stop-first is draining: fail fast, queue
  with bounded capacity/deadline, or use a different provider?
- Who owns persistent/session state migration, and what happens to a live
  audio/token stream on a forced cutover?
- How are shared SINGLETON and cross-application instances pinned or barred
  from unload, especially where consumers retained raw Python references?
- Who attests the complete artifact contents and OS-level resource release?
- Is idle-eviction a host policy, and how are cold-start penalties/hysteresis
  expressed without adding a generic machine scheduler to core?

## Explicit disposition

**KEEP** the seven experiments and their tests under `research/`.
**DO NOT MERGE** any of them into `main` as-is.
**NEXT RESEARCH TARGET:** prove immutable per-activation dependency graph
snapshots and a single authoritative catalog commit against real Invariant
objects. Then scope leased operations. This is the minimum sequence that
reduces architectural risk instead of adding framework surface area.

No production runtime files or manifest v1 schema should be edited by this
audit record.
