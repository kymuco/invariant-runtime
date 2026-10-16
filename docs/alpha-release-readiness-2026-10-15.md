# Invariant Runtime — evidence-based alpha readiness audit (2026-10-15)

**Baseline:** `main` at `e6c7a0b7840d68359a6cab41f9a52d564788882b` before this documentation-only audit.
**Package metadata:** `invariant-runtime==0.1.0`; Python >=3.11; no mandatory runtime dependencies.
**Scope:** public API, resource lifecycle and operational evidence, release packaging, documentation and consumer compatibility. This is an audit, **not** a new release, version bump, or guarantee for hostile plugins.

## Decision

| Proposed use | Decision | Evidence and qualification |
| --- | --- | --- |
| Current 0.1 alpha in a **trusted, pinned** host | **GO within tested scope** | Existing core matrix and a separately reported, private installed-wheel consumer proof. Application authorization and workflows remain outside Invariant. This external evidence is not reproducible from this public repository. |
| Publish the **next** alpha revision with new compatibility/release claims | **CONDITIONAL / NOT YET READY** | Complete the release gates below: release identity and notes, docs parity, fresh CI, installed-wheel smoke and docs build; do not claim completion from this audit |
| Promise synchronous, verified release of **all resources** whenever `close()` returns | **NO-GO** | Shutdown may be deferred until work finishes; some close exceptions are best-effort logged or swallowed |
| Publish a stable, public operational lifecycle / telemetry schema | **NO-GO** | The detailed host/supervisor/metrics/tracing modules are internal; logical close and resource drain are not externally distinct stable statuses |
| Run hostile/marketplace plugins safely with capability policy alone | **NO-GO** | Explicit trust model: in-process plugins are trusted; isolated worker is not a complete sandbox |

These decisions concern **different scopes**. A green core suite and valid real consumer integration do not establish every future deployment or release property.

## 1. What has positive evidence

### Public contracts

- `invariant.api` is the supported **host** facade; `invariant.spec` is the **plugin author** facade; root exports package `__version__`.
- `tests/core/test_public_api_contract.py` checks explicit exports and installed PEP 561 `py.typed`. `tests/core/test_public_capability_contract.py` compares the public versioned built-in references against the concrete capability catalog and Protocols.
- `PluginService` supports manifest discovery, managed plugin access, execution/workspace contexts and best-effort managed shutdown. Public host error classes are exported through `invariant.api`.
- Version-pinned capabilities are **contracts, not authority**. Policy limits still govern acquisition and operations. Manifest and source semantics remain alpha contracts.
- `ManifestSource` and `FilesystemSource` are implemented; entrypoint, ZIP and remote discovery are not ready and **must not** become release claims.

### Automated tests and downstream evidence

- GitHub Actions on the audited `main` SHA `e6c7a0b...`: [run 38698111491](https://github.com/kymuco/invariant-runtime/actions/runs/38698111491), **9/9 PASS**: Ubuntu and Windows, Python 3.11–3.14 (8 core jobs), plus installed wheel packaging smoke.
- Recent lifecycle tests cover `plugin_get` racing rollback/timeout, `RuntimeHost.close()` with in-flight calls and queued cancellation, and exact-one context/service teardown under those controlled scenarios.
- A separately installed, privately managed downstream consumer was reported to pass a scoped Windows integration check of plugin discovery, bounded operations and shutdown. Its private source, revision pins and proof artifacts are **not included** in this repository. This is historical integration evidence, not a portable compatibility guarantee or a publicly reproducible test.
- A later consumer-side compatibility update was reported to pass local tests, but the final test-count report was not supplied. Do not infer unreported counts or generalize that result.

### Security

- `SECURITY.md` expressly states trusted in-process Python, policy-scoped capability **API** boundaries, and isolated worker **process** boundaries. None are a complete hostile-code sandbox.
- No authorization, retry, workflow, companion, user consent or product goal decisions should be moved from consumers into the runtime.

## 2. Specific published-documentation discrepancy — corrected in this audit

The public API reference listed `PROCESS_CAPABILITY` as `process@0.1.0`; both the implementation in `invariant/spec/capabilities.py` and `CAPABILITIES.md` specify **`invariant:capability.process@0.2.0`**. Public tests also assert `0.2.0`.

This is a material **consumer documentation bug**: a copied `@0.1.0` manifest reference does not name the current public contract. It does **not** justify reverting the runtime's version.

Audit patch: correct the one reference row and add `tests/core/test_public_capability_docs_parity.py`, which compares all seven published built-in reference rows in both documents against `invariant.spec`. Production code and public API identifiers remain unchanged.

## 3. Operational diagnostics: what is measured and what is not

| Observation currently available | Source / scope | Correct interpretation |
| --- | --- | --- |
| Current execution table and logical close reasons | Internal `ExecutionSupervisor`, `CloseReason`, `ExecutionState` | Execution is no longer addressable; NOT a claim that every plugin thread or external effect ended |
| Counters: active/created/closed by reason, expired/failed/limit; activation and call histograms | Internal `invariant.ops.metrics.RuntimeMetrics` accessed via optional tracer; `RuntimeHost.metrics()` | Best-effort in-process metrics. `executions_active` decreases on **detachment**, before deferred `ctx.close()` may finish |
| `execution_closed` and other timeline events | Internal `InMemoryRuntimeTracer` | `execution_closed` records **logical closure**, not physical release acknowledgement; bounded in-memory event streams, not durable audit or a public API schema |
| In-flight work and deferred shutdown | Internal `ExecutionSupervisor._inflight_calls`, `_pending_contexts`, `_finalize_context` | RuntimeHost returns without waiting for an uncooperative plugin; service close is deferred until contexts drain |
| Plugin `shutdown()` and `ctx.close()` failures | `PluginService.close()` and `ExecutionSupervisor._finalize_context()` | Shutdown is explicitly best-effort: a service exception can be suppressed, while context-close failures are logged before drain count decrements |
| Capability events | `CAPABILITIES.md`; existing event-contract tests | Best-effort operation-level events; do not infer external side effect rollback or completeness of data retention |

**Operational gap, not a new bug proven by this audit:** no stable public report distinguishes
`STOPPED_CONFIRMED` from `LOGICALLY_CLOSED_BUT_DRAINING` or `RESOURCE_RELEASE_UNKNOWN`. No guarantee that a hung in-process call can be forcibly interrupted, and a closed/failed counter is **not** an authoritative resource-reclamation receipt.

Recommendation **P1, separate PR**: if a real downstream consumer needs this distinction, define a small *internal* diagnostic contract first, with tests for deferred close, `ctx.close()` exceptions, and permanently stuck calls. Do not expose a new public operational schema or advertise resource release guarantees before those tests and consumers exist.

## 4. Release gates for the next 0.x alpha

| Gate | State as of audit | Exit evidence required |
| --- | --- | --- |
| Explicit public API, errors, versioned capability refs | **Mostly PASS**, reference mismatch fixed in this branch | New docs parity regression + entire core CI green |
| Privately tested consumer integration | **Scoped historical PASS** | Independent reproduction is not possible from this repository alone; do not claim general compatibility or repeat unavailable private revision pins |
| Python 3.11–3.14 / Ubuntu and Windows core CI | **PASS for baseline** | Fresh 9/9 checks on the exact release candidate commit, not on prior `main` |
| Distribution and installed wheel smoke | **PASS for baseline** | Repeat against exact release commit |
| Documentation integrity | **Covered partly by core docs contract tests** | Optional docs extra + `zensical build --clean --strict` for release; site build is not part of the current 9-job CI workflow |
| Release identity and notes | **OPEN** | Decide next version; update `pyproject.toml`, prepare human-reviewed compatibility notes and release/tag procedure; do not publish merely by creating this audit |
| Internal shutdown diagnostics | **PARTIAL** | Record best-effort/deferred limits clearly; stronger statuses require separate implementation + tests |
| Stress/soak scenarios | **NOT a 9-job CI claim** | Run consciously selected stress/soak suite when required by deployment; default pytest options exclude these markers |
| Untrusted plugin isolation / experimental discovery backends | **OUT OF SCOPE** | Do not use either as a release feature claim |

Repository release listing returned no GitHub Releases at review time. A release tag, uploaded distribution, or publication has **not** been created by this audit.

## 5. Recommendation and next actions

1. Merge this small **documentation + regression-test** PR only after CI 9/9 PASS. It repairs a real public version-reference inconsistency without changing runtime behavior.
2. For an actual next-alpha publication, create a **separate** release-candidate branch with chosen version and release notes. Run the exact candidate matrix, documentation build, installed-wheel sample and trusted consumer smoke; confirm artifact identity before tagging.
3. Treat internal operations diagnostics as a **distinct P1 proposal** triggered by a downstream reliability need; do not conflate logical detachment with confirmed external resource release.
4. Keep source-backend expansion, hostile plugin sandboxes and generic hot-swap systems outside this release gate until explicitly scoped.

**Summary:** the tested, pinned **0.1 alpha runtime is usable within declared trust and API boundaries**. A broader next-alpha announcement is **conditional**, not automatically granted by a green CI run. Production code unchanged by this audit.
