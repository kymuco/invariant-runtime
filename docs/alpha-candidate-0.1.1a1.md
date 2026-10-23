# Invariant Runtime 0.1.1a1 — alpha candidate

**Release preparation record — not a publication announcement.** This document defines verification gates for `0.1.1a1`; neither the source version nor a successful CI build proves that a tag, GitHub Release, or PyPI distribution exists.

The candidate advances package metadata from `0.1.0` to the PEP 440 prerelease `0.1.1a1` without changing production runtime implementation or public API in the release-preparation diff. Merging code, creating a tag, and publishing artifacts are separate maintainer decisions.

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
| Source identity | Verify the checked-out exact source SHA and PR diff from public main; no unexpected production runtime code change |
| Core regression | Ubuntu and Windows, Python 3.11–3.14: full non-stress core suite, respecting documented skips |
| Built distribution | `python -m build`, install wheel `--no-deps` in new environment, run `tools/verify_installed_alpha_candidate.py` under isolated Python `-I` outside source checkout |
| Public API and version | Imported distribution, `__version__`, metadata, public manifest activation, `py.typed`, zero mandatory runtime dependencies and capability ref |
| Documentation | Strict site build in dedicated Documentation workflow, plus documentation contract tests |
| Artifacts | Upload the actual wheel, sdist, and SHA256SUMS from the verified exact source SHA; verify checksums before upload and again after download |
| Maintainer decision | Separately approve merge, tag, GitHub prerelease and/or PyPI publication; CI success alone is not release authorization |

For details of baseline evidence and caveats, read [the alpha readiness audit](alpha-release-readiness-2026-10-15.md).

## Distribution artifacts and SHA-256

The CI package job checks out the exact pull-request head (or push commit), builds one wheel and one source archive, installs the wheel in a clean environment, and runs the isolated installed-wheel and data-importer probes. It then writes `dist/SHA256SUMS`, verifies both files with `sha256sum --check`, and uploads all three to a GitHub Actions artifact named `invariant-runtime-0.1.1a1-<source-sha>`.

Expected distribution filenames:

```text
invariant_runtime-0.1.1a1-py3-none-any.whl
invariant_runtime-0.1.1a1.tar.gz
SHA256SUMS
```

After downloading and extracting the Actions artifact, verify the *individual distribution files* from the directory containing the three files:

```bash
sha256sum --check SHA256SUMS
```

The SHA-256 shown for GitHub's artifact ZIP is not a substitute for the wheel/sdist checksums. Actions artifact retention is finite; the maintainer must preserve exact verified files and record the eventual tag target SHA before a GitHub prerelease. Do not invent or precompute release digests.

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

Merging the PR, creating `v0.1.1a1`, publishing a GitHub prerelease or package-index distribution, changing downstream pins, switching runtime trust semantics, or claiming stable API compatibility each requires a separate decision. **The presence of this document or a green CI badge is not evidence that a release exists.**
