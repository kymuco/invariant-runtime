# Invariant Runtime 0.1.2a1 — alpha release candidate

**Release preparation record — not a publication announcement.** This candidate stages a subsequent alpha after the published `v0.1.1a1`; baseline source before release-only changes: `87ce994cc2222698941b95a6142062efd4f06f64`. The candidate version, successful CI, or an Actions artifact is **not** a published tag, GitHub Release, or PyPI distribution.

## Changes compared with published `0.1.1a1`

- Internal `invariant.load.plugin_store.LocalPluginStore`: content-addressed ZIP installation, physical artifact deduplication for shared plugin dependencies, durable consumer leases, managed `open_service()`, and guarded manual cleanup.
- Internal `invariant.load.sources.installed.InstalledSource`: verified installed artifacts use the existing loader, registry, and runtime service; ZIP and HTTPS are acquisition paths, not runtime sources.
- Internal `invariant.load.acquisition`: bounded HTTPS transfer with independent SHA-256 and origin verification, plus intact installation reuse.
- Internal `invariant.load.installation_plan`: exact dependency/install plan with explicit host approval of `plan_id` before any downloads.
- Internal `invariant.load.plugin_catalog` and `invariant.load.catalog_update`: pinned finite catalogs and offline metadata-admission journal with monotonic checkpoints, expiry and rollback checks.
- GitHub Pages documentation deployment with serialized workflow runs.

All new delivery helpers are **internal integrations** under `invariant.load`, **not** additions to public `invariant.api` or `invariant.spec`. Direct `ZipSource` discovery and `RemoteSource` loading remain incomplete. SHA equality does not establish publisher identity, code safety, or installation permission; this is not an untrusted-plugin sandbox, signature PKI, automatic marketplace, or hot-swap system. POSIX checkpoint durability depends on filesystem guarantees; portable Windows power-loss durability is not promised.

Public installation examples continue to pin **published** `0.1.1a1` until `0.1.2a1` is independently confirmed on PyPI. Older wheels do not contain the newly added internal Store/Catalog modules. No downstream HDE/Runplane/Character_OS repin or compatibility promise is made here.

## Candidate acceptance gates

| Gate | Evidence required |
| --- | --- |
| Source | Reviewed exact candidate HEAD and narrow release-preparation diff, without production runtime implementation edits |
| CI | Eight cross-platform jobs, Ubuntu and Windows on Python 3.11–3.14, green on exact HEAD |
| Package | Built wheel + sdist; isolated `python -I` installed-wheel smoke checks version, no mandatory runtime dependencies, public manifest activation, and internal ZIP → Store → InstalledSource activation without network |
| Docs | Strict Zensical site build, documentation-link and public-facade guards green |
| Integrity | Both distribution SHA-256 values recorded and checked against exact files downloaded from CI artifact |
| Review | Independent review and all relevant threads addressed on exact candidate HEAD |
| Authority | Separate maintainer approval to merge, tag, create GitHub prerelease and dispatch PyPI publication |

The existing `.github/workflows/publish-pypi.yml` requires an **existing** GitHub prerelease tag, exact matching tagged `pyproject.toml` version, and attached wheel + sdist + `SHA256SUMS`. It verifies these artifacts and uses manual OIDC Trusted Publishing. Its dispatch default is changed to `v0.1.2a1`; changing the default **publishes nothing**.

## Procedure after passing review

1. Squash merge the approved candidate, then capture and independently check the **new main SHA**.
2. Validate the full main CI and strict docs workflow. Download the wheel, sdist and `SHA256SUMS` from the matching post-merge package artifact and recheck hashes. Never invent or reuse old digests.
3. Explicitly tag that audited main SHA as `v0.1.2a1`; create a GitHub **prerelease** with the same three verified files. Do not rebuild different files under the same identity.
4. Only after the GitHub prerelease and checksums are confirmed, manually dispatch **Publish to PyPI**, tag `v0.1.2a1`, from main.
5. Verify published PyPI artifact availability and independently perform `python -m pip install invariant-runtime==0.1.2a1` in a fresh environment. Update the public installation quickstart only **after** actual publication.

The candidate **does not authorize** tag creation, artifact upload, publishing, downstream pin updates, or changes to trust/permission boundaries. The previous `docs/alpha-candidate-0.1.1a1.md` remains a historical record of its own release.
