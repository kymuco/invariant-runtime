# Working in Invariant Runtime

This file guides contributors and coding agents working in this repository. The
canonical project is **Invariant Runtime**, an independent, standard-library-only
Python plugin runtime. It is infrastructure for applications, not an agent,
workflow engine, marketplace, approval service, or HDE-specific component.

## Start with the contracts

- [README.md](README.md) — purpose, first run, boundaries, and alpha status.
- [docs/getting-started.md](docs/getting-started.md) and
  [examples/hello_plugin](examples/hello_plugin) — canonical first-plugin path.
- [docs/reference/public-api.md](docs/reference/public-api.md) — supported
  0.x imports, behavior, errors, and compatibility expectations.
- [docs/reference/manifest-v1.md](docs/reference/manifest-v1.md) — manifest
  fields and versioned discovery.
- [CAPABILITIES.md](CAPABILITIES.md) and [SECURITY.md](SECURITY.md) — capability
  contracts, policy limits, and trust assumptions.
- [docs/core.md](docs/core.md) — internal architecture;
  [docs/STYLE.md](docs/STYLE.md) — documentation and example standards.

## Architectural invariants

1. **Python 3.11+; no mandatory third-party runtime dependencies.**
   `pyproject.toml` declares `dependencies = []`. Keep production code
   compatible with supported Python versions and the standard library.
   `[test]` and `[docs]` are optional development environments.
2. **Respect the public boundary.** Application hosts import from
   `invariant.api`; plugin authors import from `invariant.spec`.
   The package root supplies `__version__`. Treat `invariant.load`,
   `invariant.catalog`, `invariant.exec`, `invariant.ops`, and unexported
   symbols as implementation details, not recommended integration points.
3. **Keep declarations explicit.** Plugin identity, concrete versions,
   dependencies, and sources belong in manifests. A requested capability is
   not a grant of authority: the host policy governs availability.
4. **Keep ownership separate.** Invariant owns discovery, resolution,
   runtime-owned activation/rollback, scoped capabilities, and cleanup.
   Applications own product behavior, user intent, authorization, workflow,
   consent, and any decision to retry external work.
5. **Never overstate isolation.** In-process plugins are trusted Python code.
   Capability policy is an API boundary, **not** a hostile-code sandbox.
   Process isolation alone is also insufficient for untrusted marketplace
   plugins. Read `SECURITY.md` before changing security-sensitive paths.
6. **Preserve narrow claims.** Runtime-owned rollback does not undo arbitrary
   external side effects. Alpha interfaces and experimental discovery
   backends must not be represented as universally stable or production-ready.

## Repository orientation

- `invariant/api/` — supported host facade and policies.
- `invariant/spec/` — public plugin contracts and capability references.
- `invariant/load/`, `invariant/catalog/`, `invariant/exec/`, and
  `invariant/ops/` — internal runtime layers.
- `examples/hello_plugin/` — checked introductory plugin/host example.
- `tests/core/` — contracts, security guards, and regression coverage.
- `docs/` — task-oriented guides, reference, and Zensical site content.

## Local verification

Use a Python 3.11+ environment in the repository checkout. Choose checks
proportionate to the change; do not substitute a green focused test for an
unrun full suite.

```bash
python -m pip install -e ".[test]"
python examples/hello_plugin/run.py
python -m pytest -q tests/core/test_public_api_contract.py tests/core/test_public_capability_contract.py
python -m pytest -q tests/core/test_documentation_contract.py tests/core/test_documentation_site_contract.py
```

For runtime or shared-contract changes, also run the full core suite when
practical:

```bash
python -m pytest -q tests/core
``` Default pytest options exclude the `stress` and
`soak` markers; run those suites only for a reason and with an appropriate
environment.

For documentation-site changes, optionally run:

```bash
python -m pip install -e ".[docs]"
zensical build --clean --strict
```

The README and example files remain usable without the documentation toolchain.

## Change discipline

- Prefer small, reviewable diffs; avoid opportunistic public API changes.
- For contract changes, update tests and the relevant normative reference
  together. Keep tutorial snippets aligned with checked example files.
- For filesystem, process, network, worker, or plugin-source changes, preserve
  explicit policies, bounded behavior, and security tests. Do not relax a
  security assertion just to turn a failing test green.
- Do not silently promote research experiments into production code. Record
  measured results and negative findings honestly.
- Report the exact commands and outcomes you actually observed; explicitly
  say **not run** for checks unavailable locally or due to CI limitations.
  Do not infer a passing suite from wheel-build success alone.
- Never add secrets, host-specific proof artifacts, generated site output,
  or unrelated application logic to this repository.
