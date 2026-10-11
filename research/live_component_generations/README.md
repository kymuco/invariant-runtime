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
