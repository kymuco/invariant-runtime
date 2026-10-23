"""Local plugin-store contract against the real manifest runtime."""
from __future__ import annotations
import concurrent.futures
import hashlib
import json
import os
import tempfile
import zipfile
from pathlib import Path

import pytest
from invariant.api import PluginService

from invariant.load.plugin_store import LocalPluginStore, PluginStoreError as StoreError


CORE_PY = '''from invariant.spec import BasePlugin
class Core(BasePlugin):
    def initialize(self, **deps): self.calls = 0
    def shutdown(self): pass
    def call(self):
        self.calls += 1
        return self.calls
'''
VOICE_PY = '''from invariant.spec import BasePlugin
class Voice(BasePlugin):
    def initialize(self, *, audio_core, **deps): self.core = audio_core
    def shutdown(self): pass
    def use(self): return self.core.call()
'''


def package(folder: Path, name: str, version: str, *, deps=(), payload=None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    filename = name.replace(':', '-') + '-' + version + '.zip'
    manifest = {'manifest_version': 1, 'id': name, 'version': version,
                'source': {'code_path': 'plugin.py',
                           'class_name': 'Core' if 'core' in name else 'Voice'}}
    if deps:
        manifest['dependencies'] = [{'id': dep, 'param': 'audio_core',
                                     'scope': 'SINGLETON', 'optional': False}
                                    for dep in deps]
    archive = folder / filename
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('manifest.json', json.dumps(manifest))
        z.writestr('plugin.py', payload if payload is not None else
                   (CORE_PY if 'core' in name else VOICE_PY))
    return archive


def fixtures(tmp_path):
    source = tmp_path / 'source'
    core1 = package(source, 'lab:core', '1.0.0')
    a = package(source, 'lab:voice-a', '1.0.0', deps=['lab:core@1.0.0'])
    b = package(source, 'lab:voice-b', '1.0.0', deps=['lab:core@1.0.0'])
    return core1, a, b


def test_a_then_b_deduplicates_physical_plugin_files(tmp_path):
    core1, a, b = fixtures(tmp_path)
    store = LocalPluginStore(tmp_path / 'store')
    result_a = store.install([core1, a], roots=['lab:voice-a@1.0.0'])
    assert set(result_a['new_objects']) == {'lab:core@1.0.0', 'lab:voice-a@1.0.0'}
    before = list(store.objects.iterdir())
    result_b = store.install([b], roots=['lab:voice-b@1.0.0'])
    assert result_b['new_objects'] == ['lab:voice-b@1.0.0']
    assert len(list(store.objects.iterdir())) == len(before) + 1 == 3
    assert len(store.state()['plugins']) == 3
    assert len([p for p in store.objects.rglob('manifest.json')]) == 3
    assert len([ref for ref in store.state()['plugins'] if ref == 'lab:core@1.0.0']) == 1
    # Re-adding the same plugin via the same artifact does not extract again.
    result_again = store.install([core1], roots=['lab:voice-a@1.0.0'])
    assert result_again['new_objects'] == []


def test_runtime_activates_both_plugins_with_single_dependency_instance(tmp_path):
    core1, a, b = fixtures(tmp_path)
    store = LocalPluginStore(tmp_path / 'store')
    store.install([core1, a, b], roots=['lab:voice-a@1.0.0', 'lab:voice-b@1.0.0'])
    # The existing production loader consumes the installed manifest roots.
    service = PluginService.from_manifest_paths(store.plugin_roots())
    try:
        instance_a = service.get('lab:voice-a@1.0.0')
        instance_b = service.get('lab:voice-b@1.0.0')
        assert instance_a.use() == 1
        assert instance_b.use() == 2
        assert instance_a.core is instance_b.core
    finally:
        service.close()


def test_uninstall_one_root_keeps_other_and_core(tmp_path):
    core1, a, b = fixtures(tmp_path)
    store = LocalPluginStore(tmp_path / 'store')
    store.install([core1, a, b], roots=['lab:voice-a@1.0.0', 'lab:voice-b@1.0.0'])
    state = store.uninstall_root('lab:voice-a@1.0.0')
    assert set(state['plugins']) == {'lab:core@1.0.0', 'lab:voice-b@1.0.0'}
    assert len(store.plugin_roots()) == 2
    state = store.uninstall_root('lab:voice-b@1.0.0')
    assert state['plugins'] == {}
    assert len(list(store.objects.iterdir())) == 3  # no physical deletion while consumers may run


def test_missing_dependency_has_no_partial_install(tmp_path):
    _, a, _ = fixtures(tmp_path)
    store = LocalPluginStore(tmp_path / 'store')
    with pytest.raises(StoreError, match='missing required'):
        store.install([a], roots=['lab:voice-a@1.0.0'])
    assert not list(store.objects.iterdir())
    assert store.state()['plugins'] == {}


def test_same_id_version_different_archive_refused(tmp_path):
    core1, a, b = fixtures(tmp_path)
    store = LocalPluginStore(tmp_path / 'store')
    store.install([core1, a], roots=['lab:voice-a@1.0.0'])
    original_digest = store.state()['plugins']['lab:core@1.0.0']
    other = package(tmp_path / 'other', 'lab:core', '1.0.0', payload=CORE_PY+'# patched\n')
    with pytest.raises(StoreError, match='collision'):
        store.install([other], roots=['lab:voice-a@1.0.0'])
    assert store.state()['plugins']['lab:core@1.0.0'] == original_digest


def test_same_plugin_two_versions_coexist(tmp_path):
    core1, a, b = fixtures(tmp_path)
    core2 = package(tmp_path / 'source', 'lab:core', '2.0.0', payload=CORE_PY+'# v2\n')
    c = package(tmp_path / 'source', 'lab:voice-c', '1.0.0', deps=['lab:core@2.0.0'])
    store = LocalPluginStore(tmp_path / 'store')
    store.install([core1, core2, a, c], roots=['lab:voice-a@1.0.0', 'lab:voice-c@1.0.0'])
    assert {'lab:core@1.0.0', 'lab:core@2.0.0'} <= set(store.state()['plugins'])
    assert len(list(store.objects.iterdir())) == 4


def test_zip_slip_and_digest_check_rejected(tmp_path):
    store = LocalPluginStore(tmp_path / 'store')
    evil = tmp_path / 'evil.zip'
    with zipfile.ZipFile(evil, 'w') as z:
        z.writestr('../escape.txt', 'bad')
        z.writestr('manifest.json', '{}')
    with pytest.raises(StoreError, match='unsafe'):
        store.install([evil], roots=['lab:evil@1.0.0'])
    core1, a, _ = fixtures(tmp_path)
    with pytest.raises(StoreError, match='SHA-256 mismatch'):
        store.install([core1, a], roots=['lab:voice-a@1.0.0'],
                      expected_sha256={core1.name: '0'*64})
    assert not (tmp_path / 'escape.txt').exists()


def test_parallel_install_serializes_same_artifact(tmp_path):
    core1, a, b = fixtures(tmp_path)
    store = LocalPluginStore(tmp_path / 'store')
    def install_voice(which):
        return LocalPluginStore(tmp_path / 'store').install(
            [core1, which], roots=['lab:voice-a@1.0.0' if which == a else 'lab:voice-b@1.0.0'])
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        results = list(ex.map(install_voice, [a, b]))
    assert len(store.state()['plugins']) == 3
    assert len(list(store.objects.iterdir())) == 3
    assert sum('lab:core@1.0.0' in x['new_objects'] for x in results) == 1


def test_persistence_reopens_catalog(tmp_path):
    core1, a, _ = fixtures(tmp_path)
    store = LocalPluginStore(tmp_path / 'store')
    store.install([core1, a], roots=['lab:voice-a@1.0.0'])
    reopened = LocalPluginStore(tmp_path / 'store')
    assert reopened.state() == store.state()
    assert len(reopened.plugin_roots()) == 2


def test_optional_and_capability_refs_not_installed(tmp_path):
    core1, a, b = fixtures(tmp_path)
    # For this graph, an optional plugin and capability do not require ZIP installs.
    with zipfile.ZipFile(a, 'r') as z:
        manifest = json.loads(z.read('manifest.json'))
    manifest['dependencies'].extend([
        {'id': 'lab:missing-optional@1.0.0', 'param': 'other', 'optional': True},
        {'id': 'invariant:capability.fs_sandbox@0.3.0', 'param': 'fs', 'optional': False},
    ])
    with zipfile.ZipFile(a, 'w') as z:
        z.writestr('manifest.json', json.dumps(manifest))
        z.writestr('plugin.py', VOICE_PY)
    store = LocalPluginStore(tmp_path / 'store')
    store.install([core1, a], roots=['lab:voice-a@1.0.0'])
    assert len(store.state()['plugins']) == 2


def test_same_plugin_repacked_zip_reuses_content(tmp_path):
    core1, a, _ = fixtures(tmp_path)
    # Different ZIP compression / metadata, identical payload.
    repacked = tmp_path / 'repacked.zip'
    with zipfile.ZipFile(core1) as source, zipfile.ZipFile(repacked, 'w', compression=zipfile.ZIP_DEFLATED) as out:
        for name in reversed(source.namelist()):
            out.writestr(name, source.read(name))
    assert hashlib.sha256(repacked.read_bytes()).hexdigest() != hashlib.sha256(core1.read_bytes()).hexdigest()
    store = LocalPluginStore(tmp_path / 'store')
    store.install([core1, a], roots=['lab:voice-a@1.0.0'])
    result = store.install([repacked], roots=['lab:voice-a@1.0.0'])
    assert result['new_objects'] == []
    assert len(list(store.objects.iterdir())) == 2


def test_mutated_installed_plugin_is_not_silently_reused(tmp_path):
    core1, a, _ = fixtures(tmp_path)
    store = LocalPluginStore(tmp_path / 'store')
    store.install([core1, a], roots=['lab:voice-a@1.0.0'])
    digest = store.state()['plugins']['lab:core@1.0.0']
    target = store.objects / digest / 'plugin.py'
    target.chmod(0o644)  # Simulates same-user tampering; no security sandbox.
    target.write_text('print("modified")', encoding='utf-8')
    with pytest.raises(StoreError, match='installed plugin file modified'):
        store.plugin_roots()
    with pytest.raises(StoreError, match='installed plugin file modified'):
        store.install([core1], roots=['lab:voice-a@1.0.0'])


def test_dependency_cycle_rejected(tmp_path):
    source = tmp_path / 'source'
    a = package(source, 'lab:voice-a', '1.0.0', deps=['lab:voice-b@1.0.0'])
    b = package(source, 'lab:voice-b', '1.0.0', deps=['lab:voice-a@1.0.0'])
    store = LocalPluginStore(tmp_path / 'store')
    with pytest.raises(StoreError, match='dependency cycle'):
        store.install([a, b], roots=['lab:voice-a@1.0.0'])
    assert store.state()['plugins'] == {}


def test_independent_execution_contexts_share_install_not_instance(tmp_path):
    from invariant.api import EphemeralPolicy
    core1, a, b = fixtures(tmp_path)
    # Explicitly request a dependency *per execution* rather than SINGLETON.
    for archive in (a, b):
        with zipfile.ZipFile(archive) as z:
            manifest = json.loads(z.read('manifest.json'))
            source = z.read('plugin.py')
        manifest['dependencies'][0]['scope'] = 'EXECUTION'
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr('manifest.json', json.dumps(manifest))
            z.writestr('plugin.py', source)
    store = LocalPluginStore(tmp_path / 'store')
    store.install([core1, a, b], roots=['lab:voice-a@1.0.0', 'lab:voice-b@1.0.0'])
    service = PluginService.from_manifest_paths(store.plugin_roots())
    try:
        with service.execution(policy=EphemeralPolicy()) as execution_a:
            voice_a = execution_a.get('lab:voice-a@1.0.0')
            core_a = voice_a.core
            assert voice_a.use() == 1
        with service.execution(policy=EphemeralPolicy()) as execution_b:
            voice_b = execution_b.get('lab:voice-b@1.0.0')
            core_b = voice_b.core
            assert voice_b.use() == 1
        assert core_a is not core_b
    finally:
        service.close()
    assert len(store.state()['plugins']) == 3
    assert len(list(store.objects.iterdir())) == 3


def test_install_serializes_across_real_processes(tmp_path):
    import subprocess
    import sys
    core1, a, b = fixtures(tmp_path)
    store_dir = tmp_path / 'store'
    code = '''import sys
from pathlib import Path
from invariant.load.plugin_store import LocalPluginStore
home, core, voice, plugin_id = sys.argv[1:]
store = LocalPluginStore(Path(home))
store.install([Path(core), Path(voice)], roots=[plugin_id])
'''
    env = dict(os.environ)
    env['PYTHONPATH'] = os.pathsep.join(sys.path)
    args = [
        [sys.executable, '-c', code, str(store_dir), str(core1), str(a), 'lab:voice-a@1.0.0'],
        [sys.executable, '-c', code, str(store_dir), str(core1), str(b), 'lab:voice-b@1.0.0'],
    ]
    procs = [subprocess.Popen(arg, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
             for arg in args]
    for proc in procs:
        out, err = proc.communicate(timeout=15)
        assert proc.returncode == 0, f'process failed: {err}'
    store = LocalPluginStore(store_dir)
    assert len(store.state()['plugins']) == 3
    assert len(list(store.objects.iterdir())) == 3


def test_interrupted_catalog_commit_is_recoverable(tmp_path, monkeypatch):
    import invariant.load.plugin_store as module
    core1, a, _ = fixtures(tmp_path)
    store = LocalPluginStore(tmp_path / 'store')
    real_write = module._write_json_atomic
    def crash(*args, **kwargs):
        raise RuntimeError('synthetic crash after artifact promotion')
    monkeypatch.setattr(module, '_write_json_atomic', crash)
    with pytest.raises(RuntimeError, match='synthetic crash'):
        store.install([core1, a], roots=['lab:voice-a@1.0.0'])
    assert len(list(store.objects.iterdir())) == 2
    assert store.state()['plugins'] == {}
    monkeypatch.setattr(module, '_write_json_atomic', real_write)
    done = store.install([core1, a], roots=['lab:voice-a@1.0.0'])
    assert done['new_objects'] == []
    assert len(store.state()['plugins']) == 2


def test_live_consumer_survives_logical_uninstall_until_host_closes_it(tmp_path):
    core1, a, b = fixtures(tmp_path)
    store = LocalPluginStore(tmp_path / 'store')
    store.install([core1, a], roots=['lab:voice-a@1.0.0'])
    service = PluginService.from_manifest_paths(store.plugin_roots())
    try:
        voice = service.get('lab:voice-a@1.0.0')
        assert voice.use() == 1
        store.uninstall_root('lab:voice-a@1.0.0')
        assert voice.use() == 2
        # No automatic filesystem deletion for active consumers.
        assert len(list(store.objects.iterdir())) == 2
    finally:
        service.close()
    assert len(list(store.objects.iterdir())) == 2  # physical deletion deferred


def test_invalid_manifest_rejected_before_catalog_update(tmp_path):
    folder = tmp_path / 'source'
    archive = package(folder, 'lab:core', '1.0.0')
    with zipfile.ZipFile(archive) as original:
        data = json.loads(original.read('manifest.json'))
        code = original.read('plugin.py')
    data['unexpected_manifest_field'] = True
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('manifest.json', json.dumps(data))
        z.writestr('plugin.py', code)
    store = LocalPluginStore(tmp_path / 'store')
    with pytest.raises(Exception, match='Invalid PluginMeta'):
        store.install([archive], roots=['lab:core@1.0.0'])
    assert store.state()['plugins'] == {}


def test_corrupted_catalog_rejected_without_following_digest_path(tmp_path):
    store = LocalPluginStore(tmp_path / 'store')
    store.catalog.write_text(json.dumps({
        'schema': 1, 'roots': ['lab:core@1.0.0'],
        'plugins': {'lab:core@1.0.0': '../outside'},
    }), encoding='utf-8')
    with pytest.raises(StoreError, match='catalog'):
        store.plugin_roots()


def test_symlinked_object_file_refused(tmp_path):
    core, _, _ = fixtures(tmp_path)
    store = LocalPluginStore(tmp_path / 'store')
    store.install([core], roots=['lab:core@1.0.0'])
    root = store.plugin_roots()[0]
    original = root / 'plugin.py'
    saved = tmp_path / 'external.py'
    saved.write_bytes(original.read_bytes())
    original.chmod(0o644)  # Clear Windows read-only attribute before tampering.
    original.unlink()
    try:
        original.symlink_to(saved)
    except (OSError, NotImplementedError):
        pytest.skip('OS policy forbids symlink creation')
    with pytest.raises(StoreError, match='missing or replaced'):
        store.plugin_roots()


def test_exact_version_required_for_install_roots_and_dependencies(tmp_path):
    core, a, _ = fixtures(tmp_path)
    store = LocalPluginStore(tmp_path / 'store')
    with pytest.raises(StoreError, match='exact plugin versions'):
        store.install([core, a], roots=['lab:voice-a@latest'])
    assert store.state()['plugins'] == {}


def test_capability_dependency_with_whitespace_and_case_is_host_supplied(tmp_path):
    core, voice, _ = fixtures(tmp_path)
    with zipfile.ZipFile(voice) as z:
        manifest = json.loads(z.read('manifest.json'))
        code = z.read('plugin.py')
    manifest['dependencies'].append({
        'id': ' Invariant:Capability.fs_sandbox@0.3.0 ',
        'param': 'fs',
        'scope': 'EXECUTION',
        'optional': False,
    })
    with zipfile.ZipFile(voice, 'w') as z:
        z.writestr('manifest.json', json.dumps(manifest))
        z.writestr('plugin.py', code)
    store = LocalPluginStore(tmp_path / 'store')
    store.install([core, voice], roots=['lab:voice-a@1.0.0'])
    assert set(store.state()['plugins']) == {'lab:core@1.0.0', 'lab:voice-a@1.0.0'}


@pytest.mark.parametrize('reserved', [
    '.INVARIANT-RECEIPT.JSON', 'nested/.Invariant-Receipt.Json',
])
def test_receipt_path_casefold_alias_rejected(tmp_path, reserved):
    core, _, _ = fixtures(tmp_path)
    evil = tmp_path / 'evil.zip'
    with zipfile.ZipFile(core) as source, zipfile.ZipFile(evil, 'w') as dst:
        for name in source.namelist():
            dst.writestr(name, source.read(name))
        dst.writestr(reserved, '{}')
    store = LocalPluginStore(tmp_path / 'store')
    with pytest.raises(StoreError, match='reserved receipt path'):
        store.install([evil], roots=['lab:core@1.0.0'])
    assert store.state()['plugins'] == {}
    assert list(store.objects.iterdir()) == []


def test_active_lease_prevents_physical_prune_even_after_uninstall(tmp_path):
    core, voice, _ = fixtures(tmp_path)
    store = LocalPluginStore(tmp_path / 'store')
    store.install([core, voice], roots=['lab:voice-a@1.0.0'])
    with store.leased_plugin_roots() as paths:
        assert len(paths) == 2
        service = PluginService.from_manifest_paths(paths)
        try:
            assert service.get('lab:voice-a@1.0.0').use() == 1
            store.uninstall_root('lab:voice-a@1.0.0')
            with pytest.raises(StoreError, match='untracked consumers'):
                store.prune_unreferenced()
            assert store.prune_unreferenced(confirmed_untracked_consumers_stopped=True) == []
            assert service.get('lab:voice-a@1.0.0').use() == 2
        finally:
            service.close()
    removed = store.prune_unreferenced(confirmed_untracked_consumers_stopped=True)
    assert len(removed) == 2
    assert list(store.objects.iterdir()) == []


def test_prune_preserves_installed_object_and_only_collects_orphans(tmp_path):
    core, voice, _ = fixtures(tmp_path)
    store = LocalPluginStore(tmp_path / 'store')
    store.install([core, voice], roots=['lab:voice-a@1.0.0'])
    store.uninstall_root('lab:voice-a@1.0.0')
    store.install([core], roots=['lab:core@1.0.0'])
    assert set(store.state()['plugins']) == {'lab:core@1.0.0'}
    assert len(store.prune_unreferenced(confirmed_untracked_consumers_stopped=True)) == 1
    assert len(store.plugin_roots()) == 1
    assert store.prune_unreferenced(confirmed_untracked_consumers_stopped=True) == []


def test_invalid_lease_fails_closed(tmp_path):
    core, voice, _ = fixtures(tmp_path)
    store = LocalPluginStore(tmp_path / 'store')
    store.install([core, voice], roots=['lab:voice-a@1.0.0'])
    store.uninstall_root('lab:voice-a@1.0.0')
    bad = store.leases / 'broken.json'
    bad.write_text('not json', encoding='utf-8')
    with pytest.raises(StoreError, match='unreadable consumer lease'):
        store.prune_unreferenced(confirmed_untracked_consumers_stopped=True)
    assert len(list(store.objects.iterdir())) == 2
    bad.unlink()
    assert len(store.prune_unreferenced(confirmed_untracked_consumers_stopped=True)) == 2


def test_crashed_consumer_lease_never_expires_implicitly(tmp_path):
    core, _, _ = fixtures(tmp_path)
    store = LocalPluginStore(tmp_path / 'store')
    store.install([core], roots=['lab:core@1.0.0'])
    try:
        with store.leased_plugin_roots() as roots:
            assert len(roots) == 1
            pinned = sorted(store.leases.iterdir())
            assert len(pinned) == 1
            saved = pinned[0].read_bytes()
    finally:
        pass
    pinned[0].write_bytes(saved)
    store.uninstall_root('lab:core@1.0.0')
    assert store.prune_unreferenced(confirmed_untracked_consumers_stopped=True) == []
    pinned[0].unlink()
    assert len(store.prune_unreferenced(confirmed_untracked_consumers_stopped=True)) == 1


def test_cross_process_lease_blocks_gc(tmp_path):
    import subprocess
    import sys
    core, _, _ = fixtures(tmp_path)
    home = tmp_path / 'store'
    store = LocalPluginStore(home)
    store.install([core], roots=['lab:core@1.0.0'])
    code = """import sys
from pathlib import Path
from invariant.load.plugin_store import LocalPluginStore
with LocalPluginStore(Path(sys.argv[1])).leased_plugin_roots() as paths:
    print('READY', flush=True)
    sys.stdin.readline()
"""
    env = dict(os.environ)
    env['PYTHONPATH'] = os.pathsep.join(sys.path)
    process = subprocess.Popen(
        [sys.executable, '-c', code, str(home)], env=env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout.readline().strip() == 'READY'
        store.uninstall_root('lab:core@1.0.0')
        assert store.prune_unreferenced(confirmed_untracked_consumers_stopped=True) == []
    finally:
        if process.poll() is None:
            out, err = process.communicate(input='CLOSE\n', timeout=15)
        else:
            out, err = process.communicate(timeout=15)
    assert process.returncode == 0, err
    assert len(store.prune_unreferenced(confirmed_untracked_consumers_stopped=True)) == 1

def test_prune_rejects_external_hardlinks_without_changing_permissions(tmp_path):
    import stat as statmod
    core, _, _ = fixtures(tmp_path)
    store = LocalPluginStore(tmp_path / 'store')
    store.install([core], roots=['lab:core@1.0.0'])
    obj = store.plugin_roots()[0]
    external = tmp_path / 'external-copy.py'
    try:
        os.link(obj / 'plugin.py', external)
    except (OSError, NotImplementedError):
        pytest.skip('Filesystem does not permit hardlinks')
    before = statmod.S_IMODE(external.stat().st_mode)
    store.uninstall_root('lab:core@1.0.0')
    with pytest.raises(StoreError, match='hardlinked object file'):
        store.prune_unreferenced(confirmed_untracked_consumers_stopped=True)
    assert statmod.S_IMODE(external.stat().st_mode) == before
    assert (obj / 'plugin.py').is_file()
    assert external.read_bytes() == (obj / 'plugin.py').read_bytes()


def test_open_service_automatically_leases_until_host_shutdown(tmp_path):
    core, voice, _ = fixtures(tmp_path)
    store = LocalPluginStore(tmp_path / 'store')
    store.install([core, voice], roots=['lab:voice-a@1.0.0'])
    with store.open_service() as service:
        assert len(list(store.leases.iterdir())) == 1
        assert service.get('lab:voice-a@1.0.0').use() == 1
        store.uninstall_root('lab:voice-a@1.0.0')
        assert store.prune_unreferenced(confirmed_untracked_consumers_stopped=True) == []
        assert service.get('lab:voice-a@1.0.0').use() == 2
    assert list(store.leases.iterdir()) == []
    assert len(store.prune_unreferenced(confirmed_untracked_consumers_stopped=True)) == 2


def test_open_service_construct_failure_releases_lease(tmp_path, monkeypatch):
    core, _, _ = fixtures(tmp_path)
    store = LocalPluginStore(tmp_path / 'store')
    store.install([core], roots=['lab:core@1.0.0'])
    def raise_during_discover(*args, **kwargs):
        assert len(list(store.leases.iterdir())) == 1
        raise RuntimeError('discovery failed')
    monkeypatch.setattr(PluginService, 'from_manifest_paths', raise_during_discover)
    with pytest.raises(RuntimeError, match='discovery failed'):
        with store.open_service():
            pytest.fail('should not reach service body')
    assert list(store.leases.iterdir()) == []


def test_open_service_user_exception_still_closes_before_releasing_lease(tmp_path, monkeypatch):
    core, _, _ = fixtures(tmp_path)
    store = LocalPluginStore(tmp_path / 'store')
    store.install([core], roots=['lab:core@1.0.0'])
    closed_with_lease = []
    original_close = PluginService.close
    def verify_close(self):
        closed_with_lease.append(len(list(store.leases.iterdir())) == 1)
        return original_close(self)
    monkeypatch.setattr(PluginService, 'close', verify_close)
    with pytest.raises(RuntimeError, match='host failed'):
        with store.open_service() as service:
            assert service.get('lab:core@1.0.0').call() == 1
            raise RuntimeError('host failed')
    assert closed_with_lease == [True]
    assert list(store.leases.iterdir()) == []


def test_open_service_parallel_sessions_keep_independent_leases(tmp_path):
    core, _, _ = fixtures(tmp_path)
    store = LocalPluginStore(tmp_path / 'store')
    store.install([core], roots=['lab:core@1.0.0'])
    with store.open_service() as first:
        with store.open_service() as second:
            assert len(list(store.leases.iterdir())) == 2
            assert first.get('lab:core@1.0.0').call() == 1
            assert second.get('lab:core@1.0.0').call() == 1
            store.uninstall_root('lab:core@1.0.0')
        assert len(list(store.leases.iterdir())) == 1
        assert store.prune_unreferenced(confirmed_untracked_consumers_stopped=True) == []
    assert len(store.prune_unreferenced(confirmed_untracked_consumers_stopped=True)) == 1


def test_open_service_empty_catalog_releases_lease(tmp_path):
    store = LocalPluginStore(tmp_path / 'store')
    with pytest.raises(ValueError, match='search_paths'):
        with store.open_service():
            pytest.fail('no service for empty store')
    assert list(store.leases.iterdir()) == []


def test_open_service_lease_blocks_prune_from_second_process(tmp_path):
    import subprocess
    import sys
    core, _, _ = fixtures(tmp_path)
    home = tmp_path / 'store'
    store = LocalPluginStore(home)
    store.install([core], roots=['lab:core@1.0.0'])
    child = '''import sys
from pathlib import Path
from invariant.load.plugin_store import LocalPluginStore
with LocalPluginStore(Path(sys.argv[1])).open_service() as service:
    assert service.get('lab:core@1.0.0').call() == 1
    print('READY', flush=True)
    sys.stdin.readline()
'''
    env = dict(os.environ)
    env['PYTHONPATH'] = os.pathsep.join(sys.path)
    process = subprocess.Popen(
        [sys.executable, '-c', child, str(home)], env=env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout.readline().strip() == 'READY'
        store.uninstall_root('lab:core@1.0.0')
        assert store.prune_unreferenced(confirmed_untracked_consumers_stopped=True) == []
    finally:
        if process.poll() is None:
            _, err = process.communicate(input='CLOSE\n', timeout=15)
        else:
            _, err = process.communicate(timeout=15)
    assert process.returncode == 0, err
    assert len(store.prune_unreferenced(confirmed_untracked_consumers_stopped=True)) == 1
