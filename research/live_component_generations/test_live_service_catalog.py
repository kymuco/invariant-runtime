"""Real PluginService integration; run from the research branch checkout.

Requires invariant package from the same checkout. Not a production test module.
"""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path

from invariant.api import PluginService
from catalog_epoch import CatalogConflict, StalePlan
from live_service_catalog import LiveServiceCatalog, register_live_version
from prototype import ProviderSlot


class LiveServiceCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='invariant-live-refresh-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.write_version('1.0.0')
        self.service = PluginService.from_manifest_paths([self.root])
        self.addCleanup(self.service.close)
        self.catalog = LiveServiceCatalog(self.service, roots=[self.root])
        self.slot = ProviderSlot('speech.synthesize@1')
        self.addCleanup(self.slot.deactivate)

    def write_version(self, version: str, *, dependency: str | None = None,
                      lex_dependency: str | None = None, malformed: bool = False,
                      fail_init: bool = False, no_method: bool = False):
        lex = self.root / ('lex-' + version)
        tts = self.root / ('tts-' + version)
        for p in (lex, tts): p.mkdir(exist_ok=True)
        (lex / 'plugin.py').write_text(
            'from invariant.spec import BasePlugin\n'
            'class Lexicon(BasePlugin):\n'
            '    def initialize(self, **deps): self.closed = False\n'
            '    def shutdown(self): self.closed = True\n'
            f'    def convert(self, text): return "lex-{version}:" + text\n', encoding='utf-8')
        lex_manifest = {'manifest_version':1, 'id':'research:lex', 'version':version,
            'source':{'code_path':'plugin.py','class_name':'Lexicon'}}
        if lex_dependency is not None:
            lex_manifest['dependencies'] = [{'id':lex_dependency, 'param':'tts', 'scope':'EXECUTION'}]
        (lex/'manifest.json').write_text(json.dumps(lex_manifest),encoding='utf-8')
        tts_code = ('from invariant.spec import BasePlugin\n'
            'class TTS(BasePlugin):\n'
            + ('    def initialize(self, lexicon): raise RuntimeError("broken init")\n' if fail_init else
               '    def initialize(self, lexicon): self.lexicon = lexicon; self.closed = False\n')
            + '    def shutdown(self): self.closed = True\n'
            + ('' if no_method else f'    def synthesize(self, text): return "tts-{version}:" + self.lexicon.convert(text)\n'))
        (tts/'plugin.py').write_text(tts_code, encoding='utf-8')
        (tts/'manifest.json').write_text(json.dumps({
            'manifest_version': 9 if malformed else 1,
            'id':'research:tts', 'version':version,
            'source':{'code_path':'plugin.py','class_name':'TTS'},
            'dependencies':[{'id': dependency or f'research:lex@{version}',
                             'param':'lexicon','scope':'EXECUTION'}]
        }), encoding='utf-8')

    def add_v2(self):
        self.write_version('2.0.0')
        return self.catalog.stage()

    def test_staging_does_not_touch_existing_service_or_activate_code(self):
        service_identity = id(self.service)
        plan = self.add_v2()
        self.assertEqual(self.catalog.epoch, 1)
        self.assertFalse(self.service.has_plugin('research:tts@2.0.0'))
        self.assertEqual(len(plan.added), 2)
        self.assertEqual(id(self.service), service_identity)

    def test_public_inventory_new_versions_in_same_service(self):
        plan = self.add_v2()
        self.catalog.publish(plan)
        self.assertTrue(self.service.has_plugin('research:tts@1.0.0'))
        self.assertTrue(self.service.has_plugin('research:tts@2.0.0'))
        self.assertEqual(self.catalog.epoch, 2)

    def test_two_versions_keep_exact_dependency_graphs_and_lease_shutdown(self):
        register_live_version(self.slot,self.catalog,'research:tts','1.0.0')
        self.slot.switch('research:tts','1.0.0')
        with self.slot.acquire() as previous:
            old_context, old_plugin = previous
            self.assertEqual(old_plugin.synthesize('hi'),'tts-1.0.0:lex-1.0.0:hi')
            self.assertFalse(old_plugin.closed)
            plan=self.add_v2()
            self.catalog.publish(plan)
            register_live_version(self.slot,self.catalog,'research:tts','2.0.0')
            self.slot.switch('research:tts','2.0.0')
            self.assertEqual(old_plugin.synthesize('old'),'tts-1.0.0:lex-1.0.0:old')
            self.assertFalse(old_plugin.closed)
            with self.slot.acquire() as new:
                self.assertEqual(new[1].synthesize('new'),'tts-2.0.0:lex-2.0.0:new')
                self.assertIsNot(new[0], old_context)
        self.assertTrue(old_plugin.closed)
        self.assertTrue(old_plugin.lexicon.closed)
        self.assertEqual(self.slot.shutdown_errors(),())

    def test_broken_new_manifest_cannot_publish(self):
        self.write_version('2.0.0',malformed=True)
        with self.assertRaises(Exception): self.catalog.stage()
        self.assertEqual(self.catalog.epoch,1)
        self.assertFalse(self.service.has_plugin('research:tts@2.0.0'))

    def test_missing_exact_dependency_fails_preflight(self):
        self.write_version('2.0.0', dependency='research:lex@9.0.0')
        with self.assertRaises(Exception): self.catalog.stage()
        self.assertFalse(self.service.has_plugin('research:tts@2.0.0'))

    def test_dependency_cycle_fails_preflight(self):
        self.write_version('2.0.0',lex_dependency='research:tts@2.0.0')
        with self.assertRaises(Exception): self.catalog.stage()
        self.assertEqual(self.catalog.epoch,1)

    def test_unpinned_dependency_fails_preflight(self):
        self.write_version('2.0.0',dependency='research:lex')
        with self.assertRaises(CatalogConflict): self.catalog.stage()
        self.assertEqual(self.catalog.epoch,1)

    def test_same_version_code_edit_is_rejected(self):
        (self.root/'tts-1.0.0'/'plugin.py').write_text('# edited artifact',encoding='utf-8')
        with self.assertRaises(CatalogConflict): self.catalog.stage()
        self.assertEqual(self.catalog.epoch,1)

    def test_stage_to_publish_mutation_rejected(self):
        plan = self.add_v2()
        with (self.root/'tts-2.0.0'/'plugin.py').open('a',encoding='utf-8') as f:
            f.write('\n# edited after stage\n')
        with self.assertRaises(StalePlan): self.catalog.publish(plan)
        self.assertFalse(self.service.has_plugin('research:tts@2.0.0'))

    def test_duplicate_same_version_manifest_rejected(self):
        original=self.root/'tts-1.0.0'/'manifest.json'
        duplicate=self.root/'other-tts-1.0.0'
        duplicate.mkdir()
        (duplicate/'manifest.json').write_bytes(original.read_bytes())
        (duplicate/'plugin.py').write_bytes((self.root/'tts-1.0.0'/'plugin.py').read_bytes())
        with self.assertRaises(CatalogConflict): self.catalog.stage()

    def test_managed_instance_cache_stays_unchanged_after_catalog_commit(self):
        current = self.service.get('research:tts@1.0.0')
        self.addCleanup(lambda: self.service.shutdown('research:tts@1.0.0'))
        plan = self.add_v2()
        self.catalog.publish(plan)
        self.assertIs(self.service.get('research:tts@1.0.0'),current)
        self.assertEqual(current.synthesize('hey'),'tts-1.0.0:lex-1.0.0:hey')

    def test_noop_refresh_does_not_rebuild_service(self):
        plan=self.catalog.stage()
        self.assertEqual(plan.added,())
        self.assertEqual(self.catalog.publish(plan),1)

    def test_concurrent_readers_see_full_catalog_publication(self):
        plan=self.add_v2()
        errors=[]
        stop=threading.Event()
        def reader():
            while not stop.is_set():
                inv={(p.id,p.version) for p in self.service.list_plugins()}
                if (('research:tts','2.0.0') in inv)!= (('research:lex','2.0.0') in inv):
                    errors.append(inv)
        threads=[threading.Thread(target=reader) for _ in range(4)]
        for thread in threads: thread.start()
        try:
            self.catalog.publish(plan)
        finally:
            stop.set()
            for thread in threads: thread.join(timeout=2)
        self.assertEqual(errors,[])
        self.assertTrue(all(not t.is_alive() for t in threads))

    def test_initial_unpinned_latest_has_different_semantics_after_refresh(self):
        # Known unsupported guarantee: latest is dynamic; exact refs are pinned.
        self.assertEqual(self.service.registry.resolve('research:tts').meta.version,'1.0.0')
        self.catalog.publish(self.add_v2())
        self.assertEqual(self.service.registry.resolve('research:tts').meta.version,'2.0.0')
        self.assertEqual(self.service.registry.resolve('research:tts@1.0.0').meta.version,'1.0.0')


if __name__=='__main__': unittest.main()
