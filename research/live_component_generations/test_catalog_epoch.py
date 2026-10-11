"""Pure metadata epoch tests, runnable without an Invariant checkout."""
from __future__ import annotations

import threading
import unittest
from catalog_epoch import Candidate, CatalogConflict, CatalogEpochs, StalePlan


def c(name: str, version: str, *, dependencies=(), fingerprint=None) -> Candidate:
    return Candidate(f'{name}@{version}', fingerprint or f'hash:{name}:{version}', tuple(dependencies))


class EpochTests(unittest.TestCase):
    def setUp(self):
        self.first = c('research:tts', '1.0.0', dependencies=['research:lex@1.0.0'])
        self.lex1 = c('research:lex', '1.0.0')
        self.lex2 = c('research:lex', '2.0.0')
        self.second = c('research:tts', '2.0.0', dependencies=['research:lex@2.0.0'])
        self.epochs = CatalogEpochs([self.first, self.lex1])

    def test_initial_is_one_atomic_epoch(self):
        epoch, snap = self.epochs.snapshot()
        self.assertEqual(epoch, 1)
        self.assertEqual(set(snap), {self.first.key, self.lex1.key})

    def test_monotonic_add_two_versions_together(self):
        items = [self.first, self.lex1, self.second, self.lex2]
        plan = self.epochs.stage(items)
        self.assertEqual(plan.added, (self.lex2.key, self.second.key))
        self.assertEqual(self.epochs.snapshot()[0], 1)
        self.epochs.publish(plan, items)
        self.assertEqual(self.epochs.snapshot()[0], 2)

    def test_failed_callback_does_not_publish(self):
        items = [self.first, self.lex1, self.second, self.lex2]
        plan = self.epochs.stage(items)
        def fail(*_): raise RuntimeError('blocked')
        with self.assertRaisesRegex(RuntimeError, 'blocked'):
            self.epochs.publish(plan, items, commit=fail)
        self.assertEqual(self.epochs.snapshot()[0], 1)
        self.assertNotIn(self.second.key, self.epochs.snapshot()[1])

    def test_stale_stage_after_newer_commit(self):
        items = [self.first, self.lex1, self.second, self.lex2]
        p1, p2 = self.epochs.stage(items), self.epochs.stage(items)
        self.epochs.publish(p1, items)
        with self.assertRaises(StalePlan): self.epochs.publish(p2, items)

    def test_changed_staged_artifact_refused(self):
        items = [self.first, self.lex1, self.second, self.lex2]
        plan = self.epochs.stage(items)
        mutated = [self.first, self.lex1, c('research:tts', '2.0.0',
                   dependencies=['research:lex@2.0.0'], fingerprint='mutated'), self.lex2]
        with self.assertRaises(StalePlan): self.epochs.publish(plan, mutated)
        self.assertEqual(self.epochs.snapshot()[0], 1)

    def test_same_version_edit_rejected(self):
        with self.assertRaises(CatalogConflict):
            self.epochs.stage([c('research:tts', '1.0.0', fingerprint='changed',
                               dependencies=['research:lex@1.0.0']), self.lex1])

    def test_silent_removal_rejected(self):
        with self.assertRaises(CatalogConflict): self.epochs.stage([self.lex1])

    def test_incomplete_graph_rejected_without_side_effect(self):
        with self.assertRaisesRegex(CatalogConflict, 'missing pinned'):
            self.epochs.stage([self.first, self.lex1, self.second])
        self.assertEqual(self.epochs.snapshot()[0], 1)

    def test_cycles_rejected(self):
        cyclic = c('research:lex', '2.0.0', dependencies=['research:tts@2.0.0'])
        with self.assertRaisesRegex(CatalogConflict, 'cycle'):
            self.epochs.stage([self.first, self.lex1, self.second, cyclic])

    def test_unpinned_dependency_rejected(self):
        with self.assertRaisesRegex(CatalogConflict,'unpinned'):
            self.epochs.stage([self.first,self.lex1,c('research:tts','2.0.0',
                                                       dependencies=['research:lex'])])

    def test_duplicate_version_rejected(self):
        with self.assertRaisesRegex(CatalogConflict, 'duplicate'):
            self.epochs.stage([self.first,self.lex1,self.first])

    def test_noop_preserves_epoch(self):
        plan=self.epochs.stage([self.first,self.lex1])
        self.assertFalse(plan.added)
        self.assertEqual(self.epochs.publish(plan,[self.first,self.lex1]),1)

    def test_snapshot_is_immutable(self):
        _, snap=self.epochs.snapshot()
        with self.assertRaises(TypeError): snap[self.first.key]=self.first

    def test_previous_snapshot_remains_frozen(self):
        before=self.epochs.snapshot()[1]
        items=[self.first,self.lex1,self.second,self.lex2]
        self.epochs.publish(self.epochs.stage(items),items)
        self.assertNotIn(self.second.key,before)
        self.assertIn(self.second.key,self.epochs.snapshot()[1])

    def test_concurrent_reader_never_sees_partial_graph(self):
        items=[self.first,self.lex1,self.second,self.lex2]
        stop=threading.Event()
        errors=[]
        def reader():
            while not stop.is_set():
                e,snap=self.epochs.snapshot()
                if (self.second.key in snap) != (self.lex2.key in snap): errors.append(e)
        threads=[threading.Thread(target=reader) for _ in range(3)]
        for t in threads: t.start()
        try:
            self.epochs.publish(self.epochs.stage(items),items)
        finally:
            stop.set()
            for t in threads: t.join(timeout=2)
        self.assertEqual(errors,[])
        self.assertTrue(all(not t.is_alive() for t in threads))

    def test_callback_receives_consistent_snapshot_and_only_additions(self):
        items=[self.first,self.lex1,self.second,self.lex2]
        observed=[]
        def commit(snap, added): observed.append((frozenset(snap), added))
        self.epochs.publish(self.epochs.stage(items),items,commit=commit)
        self.assertEqual(len(observed),1)
        self.assertEqual(observed[0][1], (self.lex2.key,self.second.key))


if __name__=='__main__': unittest.main()
