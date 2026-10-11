# Live component generations — research pilot v0

**Research only. Not a production feature, public API, or proposed Manifest v1
extension.** Keep this directory outside the shipped `invariant` package.
Do not merge into `main` until the invariants and integration strategy are
reviewed explicitly.

## Hypothesis

A running host can keep one stable, host-selected **functional contract** while
switching between independently installed provider generations. Calls already
started must keep their original generation alive. Installed providers must not
consume execution resources until activated.

This **functional contract** (`speech.synthesize@1` in the experiment) is
different from Invariant's existing **host capability permissions**.

## What is actually tested

- Register several provider specifications without calling their factories.
- Activate a generation; acquire a scoped lease to its instance.
- Start a replacement outside the binding lock and switch new leases atomically.
- Keep retired generations alive until their final tracked lease ends.
- Leave the active generation untouched if candidate startup fails.
- Detect concurrent switch conflicts and dispose the losing candidate.
- Drain on deactivation; surface cleanup errors in a diagnostic snapshot.
- Reject replacement that requires an already-held exclusive resource (the
  deliberate **NO-GO for parallel swap on a full GPU**, not a stop-first solution).
- Re-activate an installed older provider as a new instance.

## Run locally

Only Python standard library is needed:

```bash
python -m unittest discover -s research/live_component_generations -p "test_*.py" -v
```

The three pilots ran **37 tests successfully on Python 3.13.5 (Linux)** in a
local test environment: 10 generation binding, 15 exclusive stop-first, and
12 subprocess lifecycle tests. This does **not** prove Python 3.11/3.12/3.14
runtime compatibility, Windows behavior, Invariant integration, core-suite
results, GPU/VRAM handling, or security against untrusted code. The new files
also parse with Python 3.11 grammar, which is **not** equivalent to executing
them on 3.11. No GitHub CI run was requested for this research branch.

## What this prototype does NOT do

- Integrate with `PluginService`, manifests, real discovery, or the existing
  version/dependency resolver.
- Download/install packages; `install()` **only registers in-memory metadata**.
- Enforce a Python interface or schema; provider claims about their functional
  contract are assumed, not validated.
- Solve independent versioned artifact identity, persistence, or trust.
- Provide production-grade worker isolation, crash supervision/reconciliation,
  resource scheduling, GPU/VRAM accounting, durable state, or policy admission.
  The worker supervisor here is an **independent test adapter**, not the
  existing Invariant isolated-plugin runtime.
- Recover from side effects of a failed `start()` or arbitrary plugin code.
- Prevent a caller retaining a raw Python instance **after** a managed lease.
- Transfer live audio/stream state or handle long-running background jobs.
- Guarantee cleanup when shutdown itself fails; the failure is recorded.

## Observed design consequences

1. **Host continuity is achievable without in-place Python module reload.**
   The application can hold a stable binding and move new leases between
   generations, while existing leases stay pinned.
2. **Managed access is required for tracked drain.** Direct references obtained
   through the existing `PluginService.get()` API cannot be transparently
   intercepted or safely retired on an arbitrary schedule.
3. **Overlap is optional, not guaranteed.** Exclusive GPU or full-VRAM plugins
   cannot use parallel startup. Stop-first requires an explicit unavailability
   interval, admission/drain policy, resource release proof, and recovery path.
4. **Module caching is a separate blocker.** The current `ManifestSource`
   caches a synthetic module in `sys.modules` based on manifest path, so this
   pilot does not demonstrate live file-code replacement.
5. **No universal cleanup guarantee.** The pilot records shutdown failures;
   real process/worker ownership must be designed before promoting any of this.

## Stop-first experiment: exclusive-resource replacement

`stop_first.py` complements the first `prototype.py`. It uses the same
metadata-only `ProviderSpec` but a different, standalone `StopFirstSlot`
with one exclusive-resource binding. It never starts two instances
concurrently in the success path.

The observed sequence is:

```text
ACTIVE(v1) -> DRAINING (deny new uses) -> STOPPING(v1)
  -> AWAITING_RELEASE -> STARTING(v2) -> ACTIVE(v2)
```

- Existing managed uses finish before the old instance is stopped.
- A drain timeout **does not stop** the old instance: admissions reopen to v1.
- A stopping error or unproven resource release results in explicit
  `FAILED` and blocked new uses, **not** an unsafe new start.
- Failed v2 startup allows a best-effort restart of v1 **only after**
  the exclusive resource is again known free. Restored v1 is a **fresh
  instance**; previous in-memory state is not retained.
- A partially initialized v2 that keeps the resource is fenced to
  `FAILED`; the prototype intentionally refuses to start v1 on top.
- Concurrent replacements are serialized. Progress and failure are visible
  through `snapshot()` and phase events.

**Result:** the simple synchronous stop-first protocol supports *host
continuity*, not *provider zero downtime*. New calls fail fast while the
provider is draining, stopping, or restarting. An application may separately
buffer or reroute them; the pilot does neither.

The stop-first pilot adds **15 tests**, including delayed release,
consumer exceptions, concurrent replacement, failed stop, failed restore,
and candidate startup that leaks exclusive ownership. Together with the
first pilot this is **25 stdlib unittest cases**.

Local repetition check: the complete 25-test suite also passed **100 times
consecutively in one Python 3.13.5 process (2,500 individual executions)**.
The first attempt to repeat via many new shell-launched processes exceeded
the command's overall timeout; it did not identify a failing individual test.
No multi-process/GPU stress claim is made.

**Critical non-guarantees:** no real GPU measurements, OS process/worker
termination proof, preemption of stuck blocking `start()`/`stop()`, cross-process
resource arbitration, or trusted external health checks. The synchronous
callbacks are test doubles. A boolean `is_resource_free` probe is only as
trustworthy as its implementation. Existing Python references outside
managed leases are untracked; migration of TTS/LLM sessions is unaddressed.

## Worker process experiment: OS-owned resource, real child processes

`worker_fixture.py`, `worker_supervisor.py`, and `test_worker_process.py`
extend the **same research branch**, outside the shipped `invariant/` package.

The parent Python process stays alive throughout. Tests copy the fixture into
different temporary version directories and embed distinct code revisions
(`v1`, `v2`). Their SHA-256 digests are pinned before activation, and
subsequent mutation of staged code is rejected. The supervisor starts each
generation with `subprocess.Popen`, waits for a structured `ready` response,
and reaps the old worker before admitting the new one.

Each worker binds a real **exclusive localhost TCP port**. The test host probes
OS bind availability to demonstrate exclusivity and release after process
exit. This is a *port ownership experiment*, not a GPU/VRAM test, not a trusted
cross-process lock service, and not a general-purpose health or security proof.

The process experiment adds **12 end-to-end unittest cases**:

- distinct worker PIDs and code revisions while the host PID remains stable;
- resource never double-bound, plus unsuccessful competing allocation;
- drain of an in-flight managed lease before stopping the old worker;
- failures before/after resource acquisition, startup readiness timeout,
  and controlled fresh-v1 restoration after candidate cleanup;
- graceful shutdown, terminate escalation, and a POSIX-only SIGTERM-ignore
  scenario that escalates to kill (skipped on Windows);
- staged artifact digest mismatch rejected before startup;
- abrupt worker exit releases the OS resource, but does **not**
  automatically update the binding to FAILED.

**Verdict: WORKER_STOP_FIRST_HOST_CONTINUITY_SUPPORTED_IN_LINUX_TEST_FIXTURE.**
OS subprocess exit and exclusive port release were observed locally. A provider
unavailability window remains during switch, as required by stop-first.

**Gaps before production:** real Invariant `PluginService`, real isolated
workers, policy and dependency resolution, signed/trusted artifact acquisition,
bounded invocation/deadline contracts, background crash monitoring, OS process
tree ownership, state migration, and physical GPU release. In particular,
after an unexpected child crash, `StopFirstSlot` still reports the stale
generation as ACTIVE until the host explicitly reconciles it. The test records
this as a **known limitation**, not an automatic recovery success.

The 37-test local suite is run with the same stdlib command above. The
previous 2,500-repeat claim only applies to the original 25 tests; the 12 new
process tests have **not** been repeated 100 times.

## Worker supervision & reconciliation experiment

`supervision.py` and `test_supervision.py` add a **separate**, still
research-only observation layer on top of the existing `worker_supervisor.py`
process fixture. They do not modify `StopFirstSlot` or the production runtime.

A worker is tracked through two distinct facts: **desired state**
(`running` / `stopped`) and **observed process state** (`poll()`, PID,
exit code, and resource ownership). A small background thread invokes
`reconcile_once()` at a bounded polling interval; callers may also invoke
that method explicitly for deterministic diagnosis.

```text
RUNNING -> [unexpected process exit] -> BACKOFF -> STARTING -> RUNNING
                                       \-> RESOURCE_WAIT -> [resource free]
                                       \-> EXHAUSTED / FAULTED
[host disable] -> STOPPING -> STOPPED  (no spontaneous restart)
```

**Recovery policy:** a version-pinned worker is restarted only when the host
still wants it running and the exclusive port is free. Retries use bounded
exponential backoff and a **cumulative per-enable-session restart budget**.
Exhaustion requires explicit host intervention (`disable()` then `enable()`);
it never resets itself after a successful short-lived restart. The original
failed RPC is **not replayed**.

**Failure containment:** if an unknown launcher error makes process ownership
uncertain, the supervisor goes to `FAULTED` instead of guessing whether it
can safely launch another child. If another process owns the exclusive port,
the supervisor waits in `RESOURCE_WAIT` without consuming restart attempts.
Intentional `disable()` cancels pending restarts and reaps the child.

**Results:** the 13 new tests passed in multiple local Python **3.13.5 /
Linux** runs. They exercise actual Python child processes and an OS-bound TCP
port, unexpected exit detection, automatic restart, crash-loop exhaustion,
manual budget reset, concurrent reconciliation, occupied-resource deferral,
artifact mutation detection, explicit shutdown, and no RPC replay. Prior pilots
contributed 37 tests, so the branch now contains **50 test methods** across
the four pilots; **the complete combined 50-test suite was not rerun** in
this step. The new files were also parsed using Python 3.11 grammar, but
not executed with a Python 3.11 interpreter. No GitHub CI was requested.

**Critical limitations:** this is *not yet* a production supervisor. It
observes process exit, **not a live-but-unresponsive worker**. It lacks
heartbeat/deadline supervision, descendant process containment, cross-process
resource arbitration, durable desired-state recovery after host crashes,
real VRAM inspection, state migration, a generic dependency graph, multi-worker
routing, and integration with `PluginService`/`StopFirstSlot`. It supervises
**one pinned worker and one simulated exclusive resource**, and holds a
control lock while making bounded synchronous worker calls. The host process
continues running, but the provider can be unavailable while it recovers.

## Health monitoring: living but unresponsive workers

The fifth independent research experiment adds `health_fixture.py`,
`health_probe.py`, and `test_health_probe.py`. This is **not** wired into
`PluginService`, `StopFirstSlot`, the earlier `WorkerSupervisor`, or the
production isolated-worker machinery.

The child emits **heartbeats from a separate Python thread** while its request
loop processes RPCs. Frames share the *same stdout pipe* and are demultiplexed
by type: they are logically independent signals, **not** an independent OS
control channel. The host monitors both:

- **Process heartbeat deadline:** a live process that ceases to emit
  heartbeats becomes unhealthy. The readiness handshake resets the first
  heartbeat deadline, preventing false timeout due solely to slow Python
  interpreter startup.
- **Per-operation deadline:** a request can hang even while heartbeats
  continue. A missing RPC reply is treated as **outcome unknown**, never
  automatically replayed. Slow but on-time operations do not trigger
  restart simply because they outlast a heartbeat interval.

The research coordinator denies new calls during recovery, attempts bounded
child termination and reaping, verifies simulated exclusive-resource release
with a localhost TCP bind probe, applies backoff and a cumulative restart
budget, and supports explicit disable without spontaneous resurrection. If
child termination is unproven, it keeps the worker handle for diagnosis and
marks the slot `FAULTED`; it does **not** allocate a replacement.

**Observed local result:** the **12 new tests passed twice** on **Python
3.13.5 / Linux**, including heartbeat silence with a live process, an RPC
hang with continuing heartbeats, legitimate slow RPCs, explicit shutdown
during a hang, crash recovery without request replay, resource contention,
restart-budget exhaustion, pinned artifact mutation, and forced stop failure.
Source and tests parse under Python 3.11 grammar but were not run on a 3.11
interpreter. Research test coverage now comprises **62 test methods across
five experiments**. All 62 tests passed in their **four separate groups**:
25 binding/stop-first, 12 worker-process, 13 exit-supervision, and 12
health-monitoring checks. A full combined local run was attempted but exceeded
the overall command timeout; **the single-command 62-test combined suite is
NOT verified green**. GitHub CI was not run.

**Limitations:** heartbeats are not proof of application-level progress or
model correctness. No real TTS/LLM, VRAM measurements, cross-process lock
guarantees, descendant-process control, independent OS heartbeat transport,
nonblocking IPC writes, durable restart decisions, GPU driver failure
detection, or production API integration is demonstrated. Strict end-to-end
deadlines and cancellation semantics for live streaming are separate design
problems; host continuity does not imply that an interrupted TTS phrase
continues seamlessly. The TCP-port probe is a **research stand-in**, and the
small timeouts are test parameters, not production recommendations.

## Public-facade integration experiment (candidate; execution pending)

`public_facade_bridge.py` and `test_public_facade_bridge.py` are an
independent **sixth hypothesis test**. Unlike the first five simulated
catalog/binding experiments, this integration adapter imports the existing
production **public** `invariant.api.PluginService` and `EphemeralPolicy`,
then calls `PluginService.from_manifest_paths()`, `list_plugins()`,
`execution()`, and `ExecutionContext.get()`. No private registry or loader
APIs, new manifest fields, or runtime/core modifications are required.

The proposed experiment uses two versioned manifest *snapshots*, each with a
`research:tts` plugin and its own exact-version `research:lexicon`
dependency. `register_generation()` stores a lazy activation recipe in the
original research `ProviderSlot` (no module import at registration). When
activated, the recipe creates a **fresh** `PluginService` for that snapshot,
checks the concrete inventory, and loads a **separate ephemeral**
execution-scoped dependency graph. Retired generations close their execution
context after their last tracked `ProviderSlot.acquire()` lease, then close
the service. This avoids assuming that `PluginService.close()` alone
shuts down managed singleton instances.

Ten new `unittest` methods are authored to check:

- Metadata-only registration without instance startup.
- Manifest-based activation and exact dependency-version injection.
- A new catalog snapshot while the first service remains alive, with old
  and new calls pinned to different generations.
- Shutdown of both provider and its execution-scoped dependency only after
  final lease release.
- Candidate rejection for missing pinned dependency, dependency cycle,
  malformed manifest, missing host-required callable, and failed initialize.
- Rejection of a mismatched host functional contract before registration.

**Evidence boundary:** these files were syntax-checked with the local
Python 3.13.5 interpreter and parsed against Python 3.11 grammar. The
**10 integration tests have NOT been executed against an Invariant checkout**
in the authoring environment: the private repository is not present in the
local Python environment and GitHub connectivity is unavailable there.
Therefore **public-facade integration is NOT YET SUPPORTED BY TEST EVIDENCE**.
Previously, the other 62 tests passed in separately verified groups;
the overall 72-test branch suite has not been run. GitHub CI was not used.

Run this experiment *from a checked-out research branch*, in its local
Python environment:

```powershell
Set-Location W:\dev\invariant-runtime
git switch research/live-component-generations-v0-2026-10-11
python -m unittest discover -s research/live_component_generations -p "test_public_facade_bridge.py" -v
```

For the prior experiments, run the appropriate individual test modules.
Do not promote any research adapter to `invariant/` solely because it
passes: the next design decision is how a production catalog-refresh and
generation-binding contract should expose safe ownership and dependency
snapshots. This stand-in lacks enforced artifact immutability, shared
singleton ownership, policy admission, multi-provider routing, worker
isolation and in-flight Python reference interception.

## Live catalog refresh within ONE PluginService (research pilot 7)

Files: `catalog_epoch.py`, `test_catalog_epoch.py`,
`live_service_catalog.py`, `test_live_service_catalog.py`.

**Hypothesis:** add a new plugin and its pinned dependencies to a running
host's *existing* `PluginService` without restarting the service, calling
`registry.clear()`, rewriting existing version descriptors, or invalidating
its managed-instance cache.

The proof-of-concept separates operations:

1. `stage()` runs actual loader discovery and detached dependency-resolution
   preflight; **no registry publication or plugin activation** occurs.
2. Published metadata generations are **append-only** and keyed by exact
   `id@version` plus a fingerprint of manifest and Python source bytes.
   Removal or same-version mutation is rejected; all plugin dependencies
   must use pinned, nonoptional `EXECUTION` refs in this narrow pilot.
3. `publish(plan)` performs fresh discovery to reject stale plans and
   filesystem changes between stage/publish, then publishes the complete
   descriptor table by one pointer assignment under the existing registry
   lock. The service object, resolver wiring, and `_instances` map remain
   unchanged.
4. `register_live_version()` uses an explicit version and an
   **ephemeral** `ExecutionContext` inside the existing service. The
   previously explored ProviderSlot leases guard the old context until its
   last tracked operation finishes, and its dependencies are then shut down.

**Evidence so far:** `catalog_epoch.py` passed **16/16** Python 3.13.5
stdlib tests locally (additive epochs, graph/cycle checks, no-op, stale
stage, same-version immutability, callback failure, atomic reader snapshots).
An additional **7/7 local adapter-contract checks** passed against tiny
fake service/registry/resolver implementations; those local-only checks are
*not* production integration evidence. Source files were Python-syntax
checked locally. The **14 new real-PluginService integration tests** are
authored but have **not been run against a real Invariant checkout in this
environment**. Do not report this pilot as end-to-end verified. Previous
tests from earlier research pilots remain separate; GitHub CI was not run.

**Important restriction:** the live-service adapter deliberately accesses
`service.registry._lock` and assigns `service.registry._descriptors`.
These are *private implementation details*. This is **research only**,
not a supported plugin API, and does not establish a production refresh
contract. The actual public API currently has no in-place refresh method.
No files under `invariant/`, `tests/core/`, or `pyproject.toml` change.

**Known boundaries:**

- This demonstrates **add-only catalog publication**, not remote package
  acquisition, removal/uninstall, policy admission, or managed in-place code
  reload. Already running modules stay on their original code paths.
- `latest` remains dynamic across publication; **only exact pinned refs**
  protect old generations from dependency drift. An unrelated consumer
  resolving multiple unpinned dependencies during a concurrent refresh is
  not covered by the experiment.
- Manifest/source SHA-256 checks are useful race detection but **do not**
  make a mutable local artifact immutable. No hostile filesystem adversary,
  symlink-race closure, signed packages, or complete artifact store.
- No effect rollback, live streaming state transfer, worker supervision
  integration, singleton reference tracking, or cross-process atomicity.
- Strict research rules exclude optional, non-EXECUTION and isolated package
  dependencies. The production feature must decide graph snapshot ownership
  before relaxing those restrictions.

To run only real integration tests **from the research branch checkout**:

```powershell
python -m unittest discover -s research/live_component_generations -p "test_live_service_catalog.py" -v
```

To run the 16 stdlib tests in any Python 3.11+ environment:

```powershell
python -m unittest discover -s research/live_component_generations -p "test_catalog_epoch.py" -v
```

**Next decision:** whether core should ever expose an explicit,
transactional `prepare_catalog / publish_catalog` interface with immutable
dependency snapshots. Do not promote the private registry assignment as-is.

## Next independent experiments (not implemented)

- Link a binding to real descriptors resolved through the public
  `PluginService` surface, without depending on private runtime internals.
- Integrate cautiously with the existing isolated-plugin worker and public
  host facade, preserving its versioning, policy, IPC, and lifecycle contracts;
  do not substitute the research fixture as a production implementation.
- Investigate deferred admission/retry and resource-bounded install staging;
  neither is implemented in this prototype.
- Investigate OS process-group ownership, worker descendants, reaping during
  host crashes, cross-process resource arbitration, and real VRAM release.
- Evolve artifact staging into a durable, verified install/switch record;
  the current fixture only copies source to distinct temporary directories.
- Explore idle activation, bounded resource budgets, multi-dependency generation
  snapshots, cleanup ownership, and crash-safe activation records.

The research target is **safe, observable component evolution**, not an
ever-growing generic framework. Zero mandatory runtime dependencies and Python
3.11+ compatibility remain hard constraints for any eventual production work.
