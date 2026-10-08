# Within-load manifest path resolution — temporal behavior baseline

## Why this exists

Runplane's installed-wheel profiling isolated Windows path canonicalization
inside `invariant-runtime` manifest discovery as a significant warm-start
cost. A tempting change would skip the second calls to `Path.resolve()`
inside `ManifestSource._ensure_under_root` because the caller already
passes canonical-looking `plugin_root` and `code_path` values.

That optimization is **not yet safe to assume equivalent**.

The current code resolves the root and source code path to construct a
descriptor, then resolves them *again* for the actual containment check.
Those calls may observe a changed filesystem mapping. Skipping either one
can turn a changed path from a rejected manifest into an accepted one.

## What the new tests establish

`tests/core/test_manifest_intra_discovery_mutation_guards.py` contains
two parametrized cases and a control:

- Simulate the source path resolving inside its root first, but outside
  the root on the second resolution **during the same _read_manifest call**.
  The loader must reject the manifest.
- Simulate the plugin root canonicalization changing between the initial
  resolution and the containment recheck. The loader must reject.
- Unchanged nested source files must still yield the same canonical
  descriptor paths and plugin-root metadata.

The tests inject a deterministic replacement for `Path.resolve()`
using pytest monkeypatch only while reading a temporary test fixture.
They **do not require symlink-creation privileges**, do not use a
production path override, and restore the normal Python path function
after each test. They exercise the implementation's *observable sequence
of path-resolution operations*.

### What they do NOT establish

- An actual Windows symlink/junction race is atomic or blocked in every
  scheduler interleaving.
- `Path.resolve()` fully prevents filesystem TOCTOU attacks.
- The current loader is safe against concurrent directory renames,
  reparse-point changes, UNC/volume edge cases or hardlinks.

An atomic path-authorization guarantee generally needs additional design,
such as handle-based verification and secure file opening where supported.
These tests preserve existing behavior, not claim a stronger guarantee.

The previously merged path-security baseline still has three skipped
file-symlink tests on the user's Windows machine (WinError 1314).
Its passing NTFS junction cases are valuable but do not replace them.

## Product decision

Do **not** implement a general fast path that blindly replaces
`p.resolve().relative_to(root.resolve())` with `p.relative_to(root)`
or caches canonical paths across distinct SDK creations.

A safer next performance candidate should avoid changing this exact
containment-revalidation window. For example, investigate the cost and
equivalence of *manifest discovery enumeration* separately, while
retaining `_read_manifest` and `_ensure_under_root` unchanged.
Such an approach needs its own tests for symlink/junction directories,
discovery order, duplicate plugin inventory, edits between loads, and
missing roots before it is eligible for a runtime PR.

## Gate

Run in the `invariant-runtime` checkout:

```powershell
$python = "W:\dev\hde\runplane\.venv-r2\Scripts\python.exe"
$file = "tests/core/test_manifest_intra_discovery_mutation_guards.py"

& $python -m ruff check $file
& $python -m ruff format --check $file
& $python -m pytest $file -q

& $python -m pytest tests/core/test_manifest_path_security_equivalence.py -q -rs
& $python -m pytest tests/core/test_public_plugin_inventory.py -q
```

This is a tests/documentation-only pull request. No performance
speedup or production security improvement is claimed.
