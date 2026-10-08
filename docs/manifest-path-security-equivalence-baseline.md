# Manifest path containment — security equivalence baseline

## Motivation and scope

Installed Runplane bootstrap profiling identified the manifest loader as
the major warm-start cost. A 15-sample `cProfile` experiment under Windows
observed about **352 calls to `Path.resolve()`**, 704 Windows
`nt._getfinalpathname` calls, and 642 `nt.stat` calls during one profiled
SDK creation. The unprofiled SDK creation median was 52.0353 ms.

Source inspection found repeated resolutions in the shared
`invariant.load.sources.manifest.ManifestSource`:

1. `plugin_root = manifest_path.parent.resolve()`;
2. `code_path = (plugin_root / code_rel).resolve()`;
3. `_ensure_under_root(root, path)` resolves each again;
4. `_build_module_name` resolves the manifest path again to establish
   a stable identity.

These operations are not automatically redundant. Some are security checks
for escapes through symlinks, junctions, and traversal.

**This branch deliberately does not optimize code.** It creates observable
correctness and security regression cases before selecting a change.

## Equivalence matrix

| Case | Existing observable contract | Test |
| --- | --- | --- |
| Nested in-root source | Accepted and descriptor paths canonicalized | `accepts_nested_code` |
| Parent traversal outside root | Rejected with `PluginLoadError` | `rejects_relative_parent_escape` |
| Absolute path to outside | Rejected | `rejects_absolute_external_code` |
| Symlink out of root | Rejected | `rejects_external_symlink_target` |
| Symlink within same root | Accepted with canonical target path | `allows_symlink_into_same_plugin_root` |
| Symlink retargeted *between* loads | Subsequent load rechecks and rejects external target | `revalidates_symlink_after_retarget` |
| NTFS directory junction escape | Rejected on Windows | `rejects_external_windows_junction` |
| NTFS junction targeting the same root | Accepted on Windows | `allows_internal_windows_junction` |
| NTFS junction retargeted between loads | Next load rejects the outside target | `revalidates_junction_between_loads` |
| Windows case-insensitive filesystem | Resolves existing filename regardless of case | `windows_case_insensitive_code_resolution` |
| Missing source file | Rejected | `rejects_missing_code` |
| Oversized manifest | Rejected at 1,000,001 bytes | `rejects_oversized_manifest` |
| Invalid JSON | Rejected with `PluginLoadError` | `rejects_malformed_json` |
| Changed manifest version between loads | New version observed | `observes_changed_content_between_creates` |
| Plugin inventory order | Same ID/version projection regardless of search order | `inventory_order_is_stable_across_search_order` |
| Deleted code between loads | Second load rejects; no stale descriptor cache | `no_cross_create_cache` |

Some OS-level features are conditional: creation of symlinks may be
disallowed without Windows Developer Mode or privileges; junction creation
may be disallowed by host policy. Such tests **skip** when facilities are
unavailable. A skipped test does *not* count as security evidence.
Windows NTFS directory junctions exercise a useful subset of path-escape
and retargeting behavior without symlink privileges; they are **not**
equivalent to file symlinks or a proof against concurrent retargeting.

## First local Windows baseline (2026-10-08)

On the original candidate HEAD `8b433dde7c5e091fe75bd05a6e59d7f1868d3bcf`:

- Ruff check: PASS.
- Ruff format --check: two style-only differences.
- Focused path-security suite: **12 passed, 3 skipped**.
- Existing public inventory suite: **4 passed**.
- The three skipped cases were file symlink creation failures with
  Windows `WinError 1314` (missing symbolic-link privilege).
- The original NTFS junction escape check did **not** skip.

The subsequent branch revision fixes the formatter findings and adds
Windows junction-into-root and junction-retarget-between-loads tests to
improve non-elevated coverage. Those **new tests are not yet validated**.
The three file symlink gaps remain real and must be recorded separately.

## Important limits of this suite

- A symlink retargeted **during** a single discovery is not proved race-safe.
  The existing loader resolves some paths and then validates already
  resolved paths; this may have a TOCTOU interval. Testing retargeting
  between discoveries must not be presented as a same-discovery race proof.
- A `Path.resolve()` call is not a permission or authorization check by
  itself; containment and later activation also matter.
- On Windows, case folding and reparse-point semantics can differ from POSIX.
  A Linux-only green run cannot stand in for the Windows gates.
- A future optimization must not reuse canonical paths across independent
  `Runplane.create()` calls or plugin edits without an explicit freshness
  model and its own proof.
- The test suite is a **contract baseline**, not a complete filesystem
  adversarial model. Reviewers should include UNC paths, device paths,
  hard links and concurrent mutations in any production optimization audit.

## Gates

Run in the `kymuco/invariant-runtime` checkout, **not Runplane**:

```powershell
git rev-parse HEAD
git status --short
python -m pytest tests/core/test_manifest_path_security_equivalence.py -q
python -m pytest tests/core/test_public_plugin_inventory.py -q
```

If Ruff is installed in the developer environment:

```powershell
python -m ruff check tests/core/test_manifest_path_security_equivalence.py
python -m ruff format --check tests/core/test_manifest_path_security_equivalence.py
```

Only after results are observed can we judge whether this baseline
matches current behavior. There are **no** timing claims and no code
changes to the loader in this PR.

## Follow-on candidate — *not implemented*

A future **separate** PR may evaluate reusing canonical paths *within a
single manifest parsing operation*, but only where equivalence has been
proved against symlink/junction escapes and mutation. Keeping every
containment validation and source freshness check is non-negotiable.
Benchmark on both Windows and an additional platform before claiming
any speedup, and verify unchanged plugin inventory/descriptor order.

Avoid implementing global manifest caches or skipping path validation.
