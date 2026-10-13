# Gate C.0 — minimal production-facing owner contract (proposal only)

**State:** source audit and contract proposal, no core changes, no new exported API, not a production-ready design approval. B.4 real GPU plugin E2E PASS reported by user on Windows 2026-10-13.

## Confirmed research evidence

- Gate A.5 readiness-gated catalog/route prototype: 30/30 scoped user-Windows PASS.
- Gate B.1 owned synchronous leases and retirement: 43/43 scoped user-Windows PASS.
- Gate B.2 stop-first real plugin lifecycle / mock exclusive resource: 73/73 scoped user-Windows PASS.
- Gate B.3 native CUDA subprocess smoke: 13/13 CPU checks PASS, explicit RTX 5070 GPU smoke PASS, exit 0, v1 PID 24956 -> v2 PID 3052, 32 MiB per worker; free memory 6753 -> 6756 MiB.
- Gate B.4 native CUDA **through actual Invariant plugin initialize/shutdown**: PASS, exit 0. Epoch 2, plugin calls 1.0.0:first and 2.0.0:second, CUDA worker PIDs 25756 -> 25292, 32 MiB per worker, 6714 MiB free before, 168 MiB observed v1 drop, 6717 MiB free after.
- User-observed GPU memory statistics are **global CUDA free memory**, not per-PID VRAM ownership; do not interpret a single success as reliability, crash recovery, LLM memory state retention or service-level compatibility.

## Production source audit (read 2026-10-13, research branch)

| Existing code | Actual owner today | Unresolved integration risk |
| --- | --- | --- |
| invariant/api/service.py : PluginService.__init__, from_manifest_paths | Mutable PluginRegistry plus PluginLoader; constructs separate ManagedPolicy/ManagedRuntime | A sidecar CatalogViews alongside service.registry would introduce two authorities. PluginService has no catalog epoch/readiness-gated route owner |
| invariant/api/service.py : PluginService.get | Returns raw managed plugin object from ExecutionRuntime.get | Untracked caller references prevent safe automatic retirement. A no-new-API promise cannot truthfully include managed hot swap |
| invariant/api/service.py : PluginService.shutdown/close | Explicit per-plugin shutdown and best-effort service shutdown | Lifetime not coupled to admitted calls, worker identity or physical resource release proof |
| invariant/catalog/registry.py : managed instance cache | Instance cache colocated with mutable descriptor registry | Generation leases and descriptor publication belong to different layers today |
| invariant/exec/runtime/execution_context.py : execution, get, close | Creates runtime(s) using service.registry and service.loader; exposes raw plugin | Context selection not guaranteed to be from a single pinned catalog epoch in production |
| invariant/exec/runtime/execution_runtime.py : get and _create_and_init | DependencyResolver + re-resolution in activation, store-managed instances | Prior Gate A negative characterization reproduced graph/actual-injection drift |
| invariant/exec/runtime/execution_runtime.py : shutdown | Logs and catches per-plugin shutdown errors | A returning context.close() cannot be treated as proof of successful plugin shutdown or VRAM release |
| invariant/exec/runtime/kernel.py : shutdown_force | Explicit best-effort, suppresses exceptions on FAILED plugin | Host must retain uncertain GPU resource ownership even if kernel reported completion |
| invariant/load/isolated_proxy.py : shutdown/close | Best-effort worker shutdown and close, swallowed errors | Host needs worker PID, process exit/reap and independent resource observation before starting exclusive successor |
| tests/core/test_public_api_contract.py | Strict invariant.api.__all__ contract | Adding public API surface without product compatibility decision breaks declared exports |

## Smallest coherent owner boundary

The HOST owns a **single active binding**: (catalog epoch, exact resolved graph, initialized generation, routing state). Metadata publication and operational readiness must never advance as independent competing pointers.

An opt-in host-managed method invocation follows: select one binding atomically and acquire a lease; use the same exact graph/version for all dependencies; execute; release lease in finally. Do NOT hand out a raw plugin object that may outlive the borrow unless its entire lifetime is explicitly tracked.

Generation state for the resource scheduler is independent from plugin identity and permissions. A version's existence in the catalog is **not** its permission to run. Effective policy capabilities remain bound to the request/ExecutionContext; catalog selection never elevates authority.

Resource lane choice is explicit per host-owned resource pool:

- **Overlap-allowed:** stage candidate, initialize, check READY, publish one binding pointer, drain old leased calls, shutdown and reconcile disposal.
- **Exclusive stop-first:** stage metadata without initialization, deny new admission, wait bounded in-flight drain, stop and reap old worker, verify release with host-owned evidence, start and validate new worker, then publish; admit a deliberate unavailable window.
- If release is not independently proven, fence the lane as FAILED; do not start another owner or call it a successful rollback.

## Non-goals and unresolved proof obligations

1. Keep the current PluginService.get return type and behavior unchanged unless separately reviewed; no claim that raw get is hot-reload-safe. Prefer experimental opt-in managed invocation at host boundary, not silent semantic alteration of existing .get.
2. No automatic transfer of in-memory LLM KV cache, conversation state, TTS stream state, or arbitrary plugin-owned threads; these need an explicit handoff contract before upgrade.
3. No exclusive-GPU proof based solely on context.close(), NVIDIA free-memory reading, or file/port sentinel. Require owner PID/reap + resource observation + fenced failure with diagnostic evidence; WDDM global counters are noisy.
4. No plugin-initiated background jobs or async stream leases supported by Gate B. The synchronous method boundary is all we measured.
5. No mutable source/weight artifact replacement under the same ID@version. Current research hashes only manifest + main Python file, not weights/transitive imports. Need pinning/verification for actual model bytes.
6. Startup/stop deadlines, crash supervision and quarantine must be represented by host-owned worker states. Do not retry ambiguous side effects.
7. Policy/mixed DependencyScope handling remains production work; the research prototypes only tested mandatory EXECUTION deps.
8. Neither full core suite, packaging compatibility, multiprocess GPU ownership, nor low-load/noisy-WDDM reliability was certified by B.4.

## Promotion recommendation

**Do not merge a hot-swap implementation into main.** First choose exactly ONE real production read/ownership path through PluginService/ExecutionContext, define an opt-in call/lease boundary and an exclusive resource adapter that reports worker termination and free-memory observations. Reuse existing kernel, loader and stop-first semantics. Preserve current public API and permissions. Verify real mixed-scope and managed workloads, independent source integrity, failure injection and full baseline suite before promotion.

Most useful next experiment is a minimal **host-owned GPU-worker lane contract** on research branch that delegates to existing RealStopFirstHost rather than creating a second switcher. Before code, decide whether callers can migrate from raw managed service.get to bounded invocation without breaking existing integrations.
