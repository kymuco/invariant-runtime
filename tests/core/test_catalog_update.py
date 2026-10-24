"""Offline, pinned catalog-update admission; no publisher key or marketplace logic."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from invariant.load.catalog_update import (
    CatalogUpdateError,
    CatalogUpdateJournal,
)

UTC = timezone.utc
NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)


def payload(entries=None):
    return json.dumps({"schema_version": 1, "artifacts": entries or []}, separators=(",", ":")).encode()


def metadata(seq, catalog, *, previous=None, publisher="example.org", channel="stable",
             issued=NOW - timedelta(hours=1), expires=NOW + timedelta(days=1)):
    return json.dumps({
        "schema_version": 1, "publisher": publisher, "channel": channel,
        "sequence": seq, "previous_catalog_sha256": previous,
        "catalog_sha256": hashlib.sha256(catalog).hexdigest(),
        "issued_at": issued.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at": expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }, separators=(",", ":")).encode()


def accept(journal, metadata_bytes, catalog, *, now=NOW, bootstrap=False):
    return journal.admit(
        metadata_bytes, catalog,
        trusted_metadata_sha256=hashlib.sha256(metadata_bytes).hexdigest(),
        now=now, bootstrap=bootstrap,
    )


def journal(tmp_path, *, publisher="example.org", channel="stable"):
    return CatalogUpdateJournal(tmp_path / "trust", publisher=publisher, channel=channel)


def test_bootstrap_update_and_persistent_rollback_rejection(tmp_path):
    j = journal(tmp_path)
    c1, c2 = payload(), payload([{
        "ref": "lab:core@1.0.0", "url": "https://plugins.example/core.zip",
        "sha256": "a" * 64, "dependencies": [],
    }])
    m1 = metadata(1, c1)
    with pytest.raises(CatalogUpdateError, match="bootstrap"):
        accept(j, m1, c1)
    old = accept(j, m1, c1, bootstrap=True)
    assert old.sequence == 1
    assert len(old.catalog.offers) == 0
    m2 = metadata(2, c2, previous=old.catalog.sha256)
    new = accept(j, m2, c2, now=NOW + timedelta(minutes=1))
    assert new.sequence == 2
    assert len(new.catalog.offers) == 1
    # A new instance (or process) still refuses the older, authentic update.
    again = journal(tmp_path)
    with pytest.raises(CatalogUpdateError, match="rollback"):
        accept(again, m1, c1, now=NOW + timedelta(minutes=2))
    with pytest.raises(CatalogUpdateError, match="no longer"):
        again.assert_current(old, now=NOW + timedelta(minutes=2))
    again.assert_current(new, now=NOW + timedelta(minutes=2))


def test_identical_update_is_idempotent_but_rebootstrap_fails(tmp_path):
    j = journal(tmp_path)
    c = payload()
    m = metadata(1, c)
    first = accept(j, m, c, bootstrap=True)
    replay = accept(j, m, c, now=NOW + timedelta(minutes=1))
    assert first.metadata_sha256 == replay.metadata_sha256
    with pytest.raises(CatalogUpdateError, match="cannot bootstrap"):
        accept(j, m, c, bootstrap=True)


def test_host_trusted_metadata_digest_and_catalog_content_are_required(tmp_path):
    j = journal(tmp_path)
    c, m = payload(), metadata(1, payload())
    with pytest.raises(CatalogUpdateError, match="metadata SHA-256 mismatch"):
        j.admit(m, c, trusted_metadata_sha256="f" * 64, now=NOW, bootstrap=True)
    bad = payload([{"ref": "lab:x@1.0.0", "url": "https://plugins.example/x.zip",
                    "sha256": "a" * 64, "dependencies": []}])
    with pytest.raises(CatalogUpdateError, match="catalog bytes failed verification"):
        accept(j, m, bad, bootstrap=True)
    assert not j.checkpoint.exists()


def test_publisher_channel_mismatch_and_independent_channels(tmp_path):
    stable = journal(tmp_path)
    beta = journal(tmp_path, channel="beta")
    c = payload()
    m = metadata(1, c)
    accept(stable, m, c, bootstrap=True)
    with pytest.raises(CatalogUpdateError, match="publisher/channel"):
        accept(beta, m, c, bootstrap=True)
    b = metadata(1, c, channel="beta")
    accept(beta, b, c, bootstrap=True)
    assert stable.checkpoint != beta.checkpoint
    with pytest.raises(CatalogUpdateError, match="publisher/channel"):
        accept(journal(tmp_path, publisher="evil.net"), m, c, bootstrap=True)


@pytest.mark.parametrize("change,pattern", [
    ({"sequence": True}, "sequence"),
    ({"sequence": 0}, "sequence"),
    ({"schema_version": 2}, "schema"),
    ({"previous_catalog_sha256": "invalid"}, "previous catalog SHA-256"),
    ({"issued_at": "2026-13-44T00:00:00Z"}, "issued_at"),
    ({"expires_at": "2026-10-24T00:00:00+00:00"}, "expires_at"),
    ({"extra": "not allowed"}, "schema"),
])
def test_strict_update_envelope_fields(tmp_path, change, pattern):
    c = payload()
    obj = json.loads(metadata(1, c))
    obj.update(change)
    raw = json.dumps(obj).encode()
    with pytest.raises(CatalogUpdateError, match=pattern):
        accept(journal(tmp_path), raw, c, bootstrap=True)


def test_duplicate_keys_and_invalid_json_are_rejected(tmp_path):
    c = payload()
    valid = metadata(1, c)
    dup = valid.replace(b'"sequence":1', b'"sequence":1,"sequence":1')
    assert dup != valid
    with pytest.raises(CatalogUpdateError, match="invalid catalog update metadata"):
        accept(journal(tmp_path), dup, c, bootstrap=True)
    with pytest.raises(CatalogUpdateError, match="metadata must be"):
        accept(journal(tmp_path), b" " * 4097, c, bootstrap=True)


@pytest.mark.parametrize("issued,expires", [
    (NOW + timedelta(seconds=1), NOW + timedelta(days=1)),
    (NOW - timedelta(days=1), NOW),
    (NOW, NOW),
    (NOW - timedelta(days=31), NOW - timedelta(minutes=1)),
    (NOW, NOW + timedelta(days=31)),
])
def test_freshness_and_30_day_lifetime(tmp_path, issued, expires):
    c = payload()
    m = metadata(1, c, issued=issued, expires=expires)
    with pytest.raises(CatalogUpdateError, match="not yet valid or expired|validity must"):
        accept(journal(tmp_path), m, c, bootstrap=True)


def test_host_clock_rollback_and_expired_current_rejected(tmp_path):
    j = journal(tmp_path)
    c = payload()
    m = metadata(1, c, expires=NOW + timedelta(hours=2))
    old = accept(j, m, c, bootstrap=True)
    with pytest.raises(CatalogUpdateError, match="clock moved backwards"):
        j.assert_current(old, now=NOW - timedelta(seconds=1))
    with pytest.raises(CatalogUpdateError, match="expired"):
        j.assert_current(old, now=NOW + timedelta(hours=2))
    accept(j, m, c, now=NOW + timedelta(minutes=15))
    with pytest.raises(CatalogUpdateError, match="clock moved backwards"):
        accept(j, m, c, now=NOW + timedelta(minutes=14))
    with pytest.raises(CatalogUpdateError, match="timezone-aware"):
        j.assert_current(old, now=datetime(2026, 10, 24, 12, 0))


def test_rollback_skipped_sequence_wrong_previous_and_fork(tmp_path):
    j = journal(tmp_path)
    c1 = payload()
    accept(j, metadata(1, c1), c1, bootstrap=True)
    c2 = payload([{"ref": "lab:x@1.0.0", "url": "https://plugins.example/x.zip",
                   "sha256": "b" * 64, "dependencies": []}])
    with pytest.raises(CatalogUpdateError, match="skipped"):
        accept(j, metadata(3, c2, previous=hashlib.sha256(c1).hexdigest()), c2)
    with pytest.raises(CatalogUpdateError, match="chain"):
        accept(j, metadata(2, c2, previous="f" * 64), c2)
    accept(j, metadata(2, c2, previous=hashlib.sha256(c1).hexdigest()), c2)
    with pytest.raises(CatalogUpdateError, match="same catalog sequence"):
        accept(j, metadata(2, c1, previous=hashlib.sha256(c1).hexdigest()), c1)
    with pytest.raises(CatalogUpdateError, match="rollback"):
        accept(j, metadata(1, c1), c1)


def test_corrupt_or_symlink_checkpoint_fails_closed(tmp_path):
    j = journal(tmp_path)
    c = payload()
    m = metadata(1, c)
    j.home.mkdir(parents=True)
    j.checkpoint.write_text("{ invalid json")
    with pytest.raises(CatalogUpdateError, match="invalid catalog update checkpoint"):
        accept(j, m, c, bootstrap=True)
    j.checkpoint.unlink()
    target = tmp_path / "other.json"
    target.write_text("{}")
    try:
        j.checkpoint.symlink_to(target)
    except OSError:
        pytest.skip("creating a symlink requires platform privileges")
    with pytest.raises(CatalogUpdateError, match="symlink"):
        accept(j, m, c, bootstrap=True)
    assert target.read_text() == "{}"


def test_rollback_journal_missing_requires_reenrollment(tmp_path):
    j = journal(tmp_path)
    c = payload()
    m = metadata(1, c)
    accept(j, m, c, bootstrap=True)
    j.checkpoint.unlink()
    with pytest.raises(CatalogUpdateError, match="bootstrap"):
        accept(j, m, c)


def test_offline_admission_does_not_contact_network(tmp_path, monkeypatch):
    import urllib.request

    def forbidden(*args, **kwargs):
        pytest.fail("catalog updates must not download or execute anything")

    monkeypatch.setattr(urllib.request, "build_opener", forbidden)
    c = payload()
    j = journal(tmp_path)
    accepted = accept(j, metadata(1, c), c, bootstrap=True)
    assert accepted.catalog.offers == ()
    j.assert_current(accepted, now=NOW)


def test_expiry_rechecked_after_waiting_for_checkpoint_lock(tmp_path, monkeypatch):
    import invariant.load.catalog_update as catalog_update

    j = journal(tmp_path)
    catalog = payload()
    envelope = metadata(1, catalog, expires=NOW + timedelta(minutes=1))
    accepted = accept(j, envelope, catalog, bootstrap=True)
    # Simulate contention: the lock is acquired after the trusted clock has
    # advanced beyond the expiration. Sampling it before waiting is unsafe.
    clock = [NOW + timedelta(seconds=30)]

    class DelayedLock:
        def __enter__(self):
            clock[0] = NOW + timedelta(minutes=2)
        def __exit__(self, exc_type, exc, tb):
            return False

    old_now = catalog_update._now
    monkeypatch.setattr(catalog_update, "_now", lambda value: old_now(clock[0] if value is None else value))
    j._lock = DelayedLock()
    with pytest.raises(CatalogUpdateError, match="expired"):
        j.assert_current(accepted)
    with pytest.raises(CatalogUpdateError, match="expired"):
        j.admit(
            envelope, catalog,
            trusted_metadata_sha256=hashlib.sha256(envelope).hexdigest(),
        )
