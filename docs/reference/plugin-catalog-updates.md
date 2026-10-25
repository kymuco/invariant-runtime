# Trusted Plugin Catalog updates (internal)

This is a **local, offline admission/checkpoint mechanism** for
[Plugin Catalog v1](plugin-catalog-v1.md). It addresses sequence rollback,
time-bounded validity, and explicit host-pinned publisher/channel identity.
It does **not** discover publishers, fetch metadata, verify publisher digital
signatures, provide a trusted clock, or certify the safety of plugin code.

## Trust assumptions

The host independently obtains a trusted **SHA-256 of the exact metadata
envelope bytes**, pins the expected publisher ID and channel, protects the
persistent checkpoint directory, and supplies a reasonably trustworthy UTC
clock. Merely serving an SHA-256 next to metadata on the same untrusted server
**is not sufficient**. Publisher/channel strings are checked against the
host's pins, but are not cryptographic proof of ownership or authorship.

## Update metadata format

The metadata file (maximum 4096 bytes) is **separate** from the existing
Plugin Catalog v1 file. Its fields are exact and versioned:

```json
{
  "schema_version": 1,
  "publisher": "example.org",
  "channel": "stable",
  "sequence": 1,
  "previous_catalog_sha256": null,
  "catalog_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "issued_at": "2026-10-24T12:00:00Z",
  "expires_at": "2026-10-25T12:00:00Z"
}
```

The `catalog_sha256` value above is a **placeholder**, not the hash of a real
catalog. Sequence numbers are strictly positive integers. The initial
edition must have sequence 1 and null `previous_catalog_sha256`; an update
must use the exact next sequence and include the SHA-256 of the **previous
accepted catalog**. This binds the update history to the local checkpoint.
The metadata itself is separately bound to the independently trusted
expected metadata SHA-256.

Timestamps must use exact UTC `YYYY-MM-DDTHH:MM:SSZ` formatting. Admission
rejects future `issued_at`, expired metadata, invalid time ordering, and
validity intervals longer than 30 days. This limits time-based replay but
does not prove that a more recent edition does not already exist elsewhere.

## Admit and use

```python
from pathlib import Path
from invariant.load.catalog_update import CatalogUpdateJournal

journal = CatalogUpdateJournal(
    Path(".invariant-trust-state"),
    publisher="example.org",
    channel="stable",
)
accepted = journal.admit(
    metadata_bytes,
    catalog_bytes,
    trusted_metadata_sha256=independently_trusted_metadata_digest,
    bootstrap=True,  # Only for explicit first enrollment (sequence 1)
)

journal.assert_current(accepted)
planner = accepted.catalog.planner(
    store, allowed_origins={"https://plugins.example"},
)
plan = planner.plan(["example:voice-a@1.0.0"])

# Host must actually show the plan and obtain separate approval.
journal.assert_current(accepted)
result = planner.apply(plan, approved_plan_id=plan.plan_id)
```

For subsequent sequential editions, call `admit(...)` **without**
`bootstrap=True`. Identical unexpired metadata can be re-admitted
idempotently. An older edition, skipped sequence, mismatched predecessor,
different bytes at the same sequence, changed publisher/channel, corrupt
checkpoint, or observed host clock rollback is rejected.

The checkpoint is stored atomically, with cross-process locking, in a
host-chosen local directory. **Never delete, restore or reset this state as
a normal update:** doing so defeats rollback protection. A missing checkpoint
is not silently treated as fresh enrollment. Back up this state under the
host's protection; it is **not** tamper-proof against the host or filesystem
administrator.

`assert_current()` is a point-in-time check. Neither that call nor the
checkpoint remains locked over `planner.apply()` and its downloads; an
update admitted concurrently during network operations is outside this v1
atomicity guarantee. Hosts must handle this lifecycle and must not treat an
already-created planner as a permanently trusted authorization.

This component is not a publisher-signature PKI, trust-on-first-use registry,
marketplace update feed, or transparency log. It does not provide cryptographic
freshness against a hostile clock or a compromised trust-state directory.
Catalogs remain inert data; executable plugin installation still requires
the existing pinned ZIP validation and **separate host approval**.

See also [Installation guide](../guides/local-plugin-archives.md),
[Security](../security.md), and [Manifest v1](manifest-v1.md).
