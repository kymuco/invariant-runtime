# Invariant Runtime 0.1.1a1 — alpha candidate

**Status: PREPARED CANDIDATE ONLY. NOT TAGGED, RELEASED, OR PUBLISHED.**

This is a proposal to package the current standalone Invariant source as a PEP 440 prerelease. The package metadata advances from `0.1.0` to `0.1.1a1`; the release-candidate diff contains **no production runtime implementation or public API changes**. The public `main` remains at its previously verified version until this branch is separately reviewed and merged.

## Scope and change summary

The current source baseline already contains versioned manifest discovery, explicit plugin/capability dependencies, runtime-owned activation and teardown, public host/plugin-author APIs, and lifecycle regressions for concurrent execution. Their inclusion in the candidate is **not** a claim that these features were introduced after `0.1.0` metadata or that every feature is production-ready.

This candidate makes the release identity explicit, includes these notes, adds one candidate contract test and an independently installed-wheel smoke verification script, and extends the existing CI package job to run that script. The independent Documentation workflow already performs strict Zensical builds; no duplicate documentation job is added to the core matrix.

## Supported boundaries

- Python 3.11+, standard-library-only runtime dependencies.
- Host imports from `invariant.api`; plugin-author imports from `invariant.spec`; `invariant.__version__` reflects distribution metadata. Wheel includes `py.typed`.
- `ManifestSource` and `FilesystemSource` are implemented; entrypoint, ZIP and remote discovery are not production-ready.
- Built-in capability references remain versioned (for example, `invariant:capability.process@0.2.0`). Declaring a capability does not grant authority.
- Trusted in-process Python plugins, **not** hostile-code isolation. Runtime-owned rollback does not undo arbitrary external effects.
- Cleanup may be best-effort or deferred; logical closure does not prove immediate physical resource release. No general live hot-swap promise.
- Private historical consumer compatibility observations are not independently reproducible from this public repository. An exact new-wheel consumer compatibility claim requires its own separately authorized verification.

## Candidate acceptance gates

| Gate | Exact evidence required |
| --- | --- |
| Source identity | Review immutable head SHA and PR diff from public main; no unexpected production runtime code change |
| Core regression | Ubuntu and Windows, Python 3.11–3.14: full non-stress core suite, respecting documented skips |
| Built distribution | `python -m build`, install wheel `--no-deps` in new environment, run `tools/verify_installed_alpha_candidate.py` under isolated Python `-I` outside source checkout |
| Public API and version | Imported distribution, `__version__`, metadata, public manifest activation, `py.typed`, zero mandatory runtime dependencies and capability ref |
| Documentation | Strict site build in dedicated Documentation workflow, plus documentation contract tests |
| Artifacts | Preserve SHA-256 of the exact candidate wheel and tag target commit; do not invent checksums |
| Maintainer decision | Separately approve merge, tag, GitHub prerelease and/or PyPI publication; CI success alone is not release authorization |

For details of baseline evidence and caveats, read [the alpha readiness audit](alpha-release-readiness-2026-10-15.md).

## Reproducing focused validation

```bash
python -m pip install -e ".[test,docs]"
python -m pytest -q tests/core
zensical build --clean --strict
python -m pip install build
python -m build
```

The CI package job additionally creates a fresh virtual environment, installs the built wheel with no dependencies and executes the probe under `python -I`. It prints `INVARIANT_ALPHA_INSTALLED_WHEEL_PASS` only after the installed package check succeeds.

## Not authorized by this candidate

Creating `v0.1.1a1`, publishing to a package index, changing downstream application pins, switching runtime trust semantics or claiming stable API compatibility requires separate decisions. **The presence of this document or a green CI badge is not evidence that a release exists.**
