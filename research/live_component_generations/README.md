# Live component generations — research pilot v0

**Research only. Not a production feature, public API, or proposed Manifest v1
extension.** Keep this directory outside the shipped \`invariant\` package.
Do not merge into \`main\` until the invariants and integration strategy are
reviewed explicitly.

## Hypothesis

A running host can keep one stable, host-selected **functional contract** while
switching between independently installed provider generations. Calls already
started must keep their original generation alive. Installed providers must not
consume execution resources until activated.

This **functional contract** (\`speech.synthesize@1\` in the experiment) is
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

\`\`\`bash
python -m unittest discover -s research/live_component_generations -p "test_*.py" -v
\`\`\`

As authored, the pilot ran **10 tests successfully on Python 3.13.5** in an
isolated authoring environment. This does **not** prove Python 3.11/3.12/3.14
compatibility, Invariant integration, core-suite results, or behavior under
untrusted code. No GitHub CI run is required for this research branch.

## What this prototype does NOT do

- Integrate with \`PluginService\`, manifests, real discovery, or the existing
  version/dependency resolver.
- Download/install packages; \`install()\` **only registers in-memory metadata**.
- Enforce a Python interface or schema; provider claims about their functional
  contract are assumed, not validated.
- Solve independent versioned artifact identity, persistence, or trust.
- Provide worker isolation, timeouts, crash recovery, resource scheduling,
  exclusive leases, VRAM accounting, or stop-first replacement.
- Recover from side effects of a failed \`start()\` or arbitrary plugin code.
- Prevent a caller retaining a raw Python instance **after** a managed lease.
- Transfer live audio/stream state or handle long-running background jobs.
- Guarantee cleanup when shutdown itself fails; the failure is recorded.

## Observed design consequences

1. **Host continuity is achievable without in-place Python module reload.**
   The application can hold a stable binding and move new leases between
   generations, while existing leases stay pinned.
2. **Managed access is required for tracked drain.** Direct references obtained
   through the existing \`PluginService.get()\` API cannot be transparently
   intercepted or safely retired on an arbitrary schedule.
3. **Overlap is optional, not guaranteed.** Exclusive GPU or full-VRAM plugins
   cannot use parallel startup. Stop-first requires an explicit unavailability
   interval, admission/drain policy, resource release proof, and recovery path.
4. **Module caching is a separate blocker.** The current \`ManifestSource\`
   caches a synthetic module in \`sys.modules\` based on manifest path, so this
   pilot does not demonstrate live file-code replacement.
5. **No universal cleanup guarantee.** The pilot records shutdown failures;
   real process/worker ownership must be designed before promoting any of this.

## Next independent experiments (not implemented)

- Link a binding to real descriptors resolved through the public
  \`PluginService\` surface, without depending on private runtime internals.
- Add a stop-first / deferred protocol with explicit exclusive resource claims,
  no double allocation, admission control, timeouts, and failed-restart recovery.
- Test worker generations and immutable artifact paths so replacing code never
  requires mutating \`sys.modules\` or an active plugin directory.
- Explore idle activation, bounded resource budgets, multi-dependency generation
  snapshots, cleanup ownership, and crash-safe activation records.

The research target is **safe, observable component evolution**, not an
ever-growing generic framework. Zero mandatory runtime dependencies and Python
3.11+ compatibility remain hard constraints for any eventual production work.
