"""Install a tiny synthetic Epic game through the real bridge and downloader."""

import json
import os
import select
import socketserver
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from hashlib import sha1
from pathlib import Path

from legendary.models.chunk import Chunk
from legendary.models.manifest import (
    CDL,
    FML,
    ChunkInfo,
    ChunkPart,
    CustomFields,
    FileManifest,
    Manifest,
    ManifestMeta,
)
from legendary.services.errors import ServiceError
from legendary.services.importer import scan_files
from legendary.services.install import check_manifest_paths, verify_files


def fixture_game(version='1', bad_hash=False, empty=False):
    manifest = Manifest()
    manifest.meta = ManifestMeta()
    manifest.meta.app_name = 'fixture-game'
    manifest.meta.build_version = version
    manifest.meta.launch_exe = 'game.exe'
    manifest.chunk_data_list, manifest.file_manifest_list = CDL(), FML()
    manifest.custom_fields = CustomFields()
    chunks, files = {}, {}
    for filename in (() if empty else ('assets.bin', 'game.exe')):
        payload = (filename + version).encode() * 1000
        chunk = Chunk()
        chunk.data = payload
        info = ChunkInfo()
        info.guid, info.hash, info.sha_hash = chunk.guid, chunk.hash, chunk.sha_hash
        info.window_size = len(chunk.data)
        wire_data = chunk.write()
        info.file_size = len(wire_data)
        file = FileManifest()
        file.filename, file.file_size = filename, len(payload)
        file.hash = sha1(b'incorrect' if bad_hash else payload).digest()
        file.chunk_parts = [ChunkPart(guid=chunk.guid, offset=0, size=len(payload))]
        manifest.chunk_data_list.elements.append(info)
        manifest.file_manifest_list.elements.append(file)
        chunks['/data/' + info.path] = wire_data
        files[filename] = payload
    empty_file = FileManifest()
    empty_file.filename, empty_file.hash = 'empty.dat', sha1(b'').digest()
    manifest.file_manifest_list.elements.append(empty_file)
    files['empty.dat'] = b''
    return manifest.write(), chunks, files


class LocalEpic(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self):
        self.manifest, self.chunks, self.files = fixture_game()
        self.version = '1'
        self.requests = []
        self.slow_path = None
        self.gate = threading.Event()
        self.gate.set()
        fixture = Path(__file__).parent / 'fixtures'
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(fixture / 'install-test-cert.pem', fixture / 'install-test-key.pem')

        class Proxy(socketserver.BaseRequestHandler):
            def handle(handler):
                try:
                    handler.request.settimeout(10)
                    raw = b''
                    while b'\r\n\r\n' not in raw:
                        part = handler.request.recv(4096)
                        if not part:
                            return
                        raw += part
                    handler.request.sendall(b'HTTP/1.1 200 Connection established\r\n\r\n')
                    with context.wrap_socket(handler.request, server_side=True) as connection:
                        request = b''
                        while b'\r\n\r\n' not in request:
                            part = connection.recv(4096)
                            if not part:
                                return
                            request += part
                        path = request.split(b' ')[1].decode().split('?')[0]
                        self.requests.append(path)
                        code, payload = self.response(path)
                        if path == self.slow_path:
                            self.gate.wait(timeout=20)
                        connection.sendall(f'HTTP/1.1 {code} Response\r\nContent-Length: {len(payload)}\r\nConnection: close\r\n\r\n'.encode() + payload)
                except (OSError, ssl.SSLError):
                    pass  # Cancelling a download closes its in-flight connection.

        super().__init__(('127.0.0.1', 0), Proxy)

    def response(self, path):
        if path == '/account/api/oauth/verify':
            data = {'account_id': 'test-account', 'displayName': 'Install Test'}
        elif path in ('/launcher/api/public/assets/Windows', '/launcher/api/public/assets/Mac'):
            data = [{'appName': 'fixture-game', 'assetId': 'fixture', 'buildVersion': self.version,
                     'catalogItemId': 'fixture', 'namespace': 'test-ns'}]
        elif path.startswith('/entitlement/api/'):
            data = []
        elif path.startswith('/launcher/api/public/assets/v2/'):
            data = {'elements': [{'hash': sha1(self.manifest).hexdigest(), 'manifests': [
                {'uri': 'https://launcher-public-service-prod06.ol.epicgames.com/data/fixture.manifest'},
            ]}]}
        elif path == '/data/fixture.manifest':
            return 200, self.manifest
        elif path in self.chunks:
            return 200, self.chunks[path]
        else:
            return 404, b'{}'
        return 200, json.dumps(data).encode()


class InstallIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='legendary-install-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / 'config'
        (self.config / 'metadata').mkdir(parents=True)
        (self.config / 'config.ini').write_text('[Legendary]\ndisable_update_check=true\nmax_memory=64\nmax_workers=1\nlocale=en_US\n')
        (self.config / 'user.json').write_text(json.dumps({
            'account_id': 'test-account', 'access_token': 'test-access', 'refresh_token': 'test-refresh',
            'displayName': 'Install Test', 'expires_at': '2099-01-01T00:00:00Z',
        }))
        (self.config / 'metadata/fixture-game.json').write_text(json.dumps({
            'app_name': 'fixture-game', 'app_title': 'Fixture Game',
            'metadata': {'id': 'fixture', 'namespace': 'test-ns'},
            'asset_infos': {'Windows': {'app_name': 'fixture-game', 'build_version': '1'}},
        }))
        self.server = LocalEpic()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        environment = {key: value for key, value in os.environ.items() if key.lower() not in {
            'https_proxy', 'http_proxy', 'all_proxy', 'no_proxy', 'requests_ca_bundle', 'curl_ca_bundle',
        }}
        environment.update(LEGENDARY_CONFIG_PATH=str(self.config), NO_PROXY='',
                           REQUESTS_CA_BUNDLE=str(Path(__file__).parent / 'fixtures/install-test-cert.pem'),
                           HTTPS_PROXY=f'http://127.0.0.1:{self.server.server_address[1]}')
        command = [os.environ['LEGENDARY_TEST_BRIDGE']] if os.environ.get('LEGENDARY_TEST_BRIDGE') else [sys.executable, '-u', '-m', 'legendary.bridge']
        self.log = (self.root / 'bridge.log').open('w+')
        self.addCleanup(self.log.close)
        self.process = subprocess.Popen(command, env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=self.log, text=True, encoding='utf-8')
        self.addCleanup(self.stop_bridge)
        self.directory = self.root / 'Games/Fixture'

    def stop_server(self):
        self.server.gate.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def stop_bridge(self):
        if self.process.poll() is None:
            self.process.stdin.close()
            try:
                self.process.wait(timeout=25)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.process.stdout.close()

    def request(self, method, params=None):
        self.process.stdin.write(json.dumps({'id': 1, 'method': method, 'params': params or {}}) + '\n')
        self.process.stdin.flush()
        # select is used only on POSIX; Windows pipe readiness is covered by subprocess timeout in CI.
        if os.name != 'nt' and not select.select([self.process.stdout], [], [], 15)[0]:
            self.log.seek(0)
            self.fail('Bridge did not answer: ' + self.log.read())
        response = json.loads(self.process.stdout.readline())
        self.assertNotIn('error', response, response)
        return response['result']

    def wait_for(self, *states):
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            task = self.request('install.status')['task']
            if task and task['state'] in states:
                return task
            if task and task['state'] == 'failed':
                self.log.seek(0)
                self.fail(f'Installation failed: {task}\n{self.log.read()}')
            time.sleep(0.1)
        self.log.seek(0)
        self.fail('Installation timed out: ' + self.log.read())

    def prepare(self):
        task = self.request('install.prepare', {'appName': 'fixture-game', 'platform': 'Windows', 'installPath': str(self.directory)})['task']
        self.assertEqual(task['state'], 'preparing')
        return self.wait_for('ready', 'failed')

    def existing_game(self):
        self.directory.mkdir(parents=True)
        for name, payload in self.server.files.items():
            (self.directory / name).write_bytes(payload)
        (self.directory / 'save.dat').write_bytes(b'keep save data')

    def prepare_import(self, platform='Windows'):
        self.request('import.prepare', {'appName': 'fixture-game', 'platform': platform,
                                        'installPath': str(self.directory)})
        return self.wait_for('ready', 'failed')

    def test_import_verifies_existing_files_and_requires_confirmation_without_downloading(self):
        self.existing_game()
        before = {file.name: (file.read_bytes(), file.stat().st_mtime_ns) for file in self.directory.iterdir()}
        task = self.prepare_import()
        self.assertEqual(task['operation'], 'import')
        self.assertFalse(task['plan']['failures'])
        self.assertEqual(task['plan']['importSummary']['matchedFiles'], 3)
        self.assertEqual(task['plan']['downloadSize'], 0)
        self.assertFalse((self.config / 'installed.json').exists())
        self.request('install.start', {'taskId': task['id']})
        self.wait_for('completed')
        installed = self.request('library.installed')['games'][0]
        self.assertFalse(installed['needsVerification'])
        self.assertEqual(installed['installPath'], str(self.directory))
        self.assertEqual({file.name: (file.read_bytes(), file.stat().st_mtime_ns)
                          for file in self.directory.iterdir()}, before)
        self.assertFalse(set(self.server.requests) & self.server.chunks.keys())
        # The registered manifest also supports the existing update path.
        task = self.prepare()
        self.assertTrue(task['plan']['updating'])
        self.assertEqual(task['plan']['downloadSize'], 0)
        self.request('install.cancel', {'taskId': task['id']})
        self.wait_for('cancelled')

    def test_incomplete_import_reports_missing_and_corrupt_files_without_registering(self):
        self.existing_game()
        (self.directory / 'assets.bin').unlink()
        payload = self.server.files['game.exe']
        (self.directory / 'game.exe').write_bytes(b'x' * len(payload))
        task = self.prepare_import()
        summary = task['plan']['importSummary']
        self.assertEqual(summary['matchedFiles'], 1)
        self.assertEqual(summary['missingFiles'], 1)
        self.assertEqual(summary['changedFiles'], 1)
        self.assertTrue(task['plan']['failures'])
        self.assertFalse((self.config / 'installed.json').exists())
        self.assertEqual((self.directory / 'game.exe').read_bytes(), b'x' * len(payload))
        self.process.stdin.write(json.dumps({'id': 1, 'method': 'install.start',
                                            'params': {'taskId': task['id']}}) + '\n')
        self.process.stdin.flush()
        self.assertEqual(json.loads(self.process.stdout.readline())['error']['code'], 'INSTALL_REQUIREMENTS')

    def test_import_cancellation_preserves_directory_and_install_records(self):
        self.existing_game()
        task = self.prepare_import()
        self.request('install.cancel', {'taskId': task['id']})
        self.wait_for('cancelled')
        self.assertFalse((self.config / 'installed.json').exists())
        self.assertEqual((self.directory / 'save.dat').read_bytes(), b'keep save data')
        self.assertFalse(set(self.server.requests) & self.server.chunks.keys())

    def test_import_rejects_files_changed_while_reviewing(self):
        self.existing_game()
        task = self.prepare_import()
        (self.directory / 'game.exe').write_bytes(b'changed after scan')
        self.request('install.start', {'taskId': task['id']})
        task = self.wait_for('failed')
        self.assertEqual(task['error']['code'], 'IMPORT_FILES_CHANGED')
        self.assertFalse((self.config / 'installed.json').exists())

    def test_import_rebinds_a_moved_installation_and_preserves_save_location(self):
        self.existing_game()
        old = {'app_name': 'fixture-game', 'title': 'Fixture Game', 'version': '1',
               'install_path': str(self.root / 'old-location'), 'platform': 'Windows',
               'save_path': str(self.root / 'saves')}
        (self.config / 'installed.json').write_text(json.dumps({'fixture-game': old}))
        (self.config / 'manifests').mkdir(exist_ok=True)
        (self.config / 'manifests/fixture-game_1.manifest').write_bytes(self.server.manifest)
        self.server.version = '2'
        self.server.manifest, self.server.chunks, self.server.files = fixture_game(version='2')
        task = self.prepare_import()
        self.assertEqual(task['plan']['version'], '1')
        self.assertEqual(task['plan']['previousPath'], old['install_path'])
        self.assertEqual(json.loads((self.config / 'installed.json').read_text())['fixture-game'], old)
        self.request('install.start', {'taskId': task['id']})
        self.wait_for('completed')
        installed = json.loads((self.config / 'installed.json').read_text())['fixture-game']
        self.assertEqual(installed['install_path'], str(self.directory))
        self.assertEqual(installed['save_path'], old['save_path'])

    def test_import_uses_old_epic_manifest_when_files_match_that_version(self):
        self.existing_game()
        store = self.directory / '.egstore'
        store.mkdir()
        (store / 'local.mancpn').write_text(json.dumps({'AppName': 'fixture-game'}))
        (store / 'local.manifest').write_bytes(self.server.manifest)
        self.server.version = '2'
        self.server.manifest, self.server.chunks, self.server.files = fixture_game(version='2')
        task = self.prepare_import()
        self.assertEqual(task['plan']['version'], '1')
        self.assertTrue(task['plan']['warnings'])
        self.request('install.start', {'taskId': task['id']})
        self.wait_for('completed')
        self.assertEqual(self.request('library.installed')['games'][0]['version'], '1')

    def test_import_uses_selected_platform_for_remote_manifest(self):
        self.existing_game()
        metadata_path = self.config / 'metadata/fixture-game.json'
        metadata = json.loads(metadata_path.read_text())
        metadata['asset_infos']['Mac'] = metadata['asset_infos']['Windows']
        metadata_path.write_text(json.dumps(metadata))
        task = self.prepare_import(platform='Mac')
        self.assertEqual(task['plan']['platform'], 'Mac')
        self.assertTrue(any('/Mac/' in path for path in self.server.requests), self.server.requests)
        self.request('install.start', {'taskId': task['id']})
        self.wait_for('completed')
        self.assertEqual(self.request('library.installed')['games'][0]['platform'], 'Mac')

    def test_import_rejects_missing_directory_and_external_file_links(self):
        task = self.prepare_import()
        self.assertEqual(task['error']['code'], 'IMPORT_PATH_MISSING')
        self.existing_game()
        outside = self.root / 'external.exe'
        outside.write_bytes(self.server.files['game.exe'])
        (self.directory / 'game.exe').unlink()
        (self.directory / 'game.exe').symlink_to(outside)
        task = self.prepare_import()
        self.assertEqual(task['error']['code'], 'INSTALL_PATH_INVALID')
        self.assertFalse((self.config / 'installed.json').exists())

    def test_import_rejects_manifest_for_another_game(self):
        self.existing_game()
        store = self.directory / '.egstore'
        store.mkdir()
        (store / 'local.mancpn').write_text(json.dumps({'AppName': 'fixture-game'}))
        manifest = Manifest.read_all(self.server.manifest)
        manifest.meta.app_name = 'another-game'
        (store / 'local.manifest').write_bytes(manifest.write())
        task = self.prepare_import()
        self.assertEqual(task['error']['code'], 'IMPORT_MANIFEST_INVALID')
        self.assertFalse((self.config / 'installed.json').exists())

    def test_install_and_import_share_one_active_task(self):
        self.existing_game()
        task = self.prepare_import()
        self.process.stdin.write(json.dumps({'id': 1, 'method': 'install.prepare', 'params': {
            'appName': 'fixture-game', 'platform': 'Windows', 'installPath': str(self.root / 'new-game'),
        }}) + '\n')
        self.process.stdin.flush()
        self.assertEqual(json.loads(self.process.stdout.readline())['error']['code'], 'INSTALL_BUSY')
        self.request('install.cancel', {'taskId': task['id']})
        self.wait_for('cancelled')

    def test_download_verify_and_register_only_after_confirmation(self):
        options = self.request('install.options', {'appName': 'fixture-game', 'platform': 'Windows'})
        self.assertFalse(options['updating'])
        task = self.prepare()
        self.assertFalse(task['plan']['failures'])
        self.assertGreater(task['plan']['downloadSize'], 0)
        self.assertFalse((self.config / 'installed.json').exists())
        self.assertFalse(self.directory.exists())
        self.request('install.start', {'taskId': task['id']})
        self.wait_for('completed')
        installed = self.request('library.installed')['games']
        self.assertEqual(installed[0]['appName'], 'fixture-game')
        for name, payload in self.server.files.items():
            self.assertEqual((self.directory / name).read_bytes(), payload)

    def test_cancel_before_confirmation_does_not_write_game_files(self):
        task = self.prepare()
        self.request('install.cancel', {'taskId': task['id']})
        self.wait_for('cancelled')
        self.assertFalse(self.directory.exists())
        self.assertFalse((self.config / 'installed.json').exists())

    def test_writer_failure_never_registers_installation(self):
        task = self.prepare()
        (self.directory / 'game.exe').mkdir(parents=True)
        self.request('install.start', {'taskId': task['id']})
        task = self.wait_for('failed')
        self.assertEqual(task['error']['code'], 'INSTALL_DOWNLOAD_FAILED')
        self.assertFalse((self.config / 'installed.json').exists())

    def test_corrupt_files_fail_final_hash_verification(self):
        self.server.manifest, self.server.chunks, self.server.files = fixture_game(bad_hash=True)
        task = self.prepare()
        self.request('install.start', {'taskId': task['id']})
        task = self.wait_for('failed')
        self.assertEqual(task['error']['code'], 'INSTALL_VERIFY_FAILED')
        self.assertFalse((self.config / 'installed.json').exists())

    def test_cancel_download_then_resume_completed_files(self):
        paths = list(self.server.chunks)
        self.server.slow_path = paths[1]
        self.server.gate.clear()
        task = self.prepare()
        self.request('install.start', {'taskId': task['id']})
        deadline = time.monotonic() + 15
        while not (self.config / 'tmp/fixture-game.resume').exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertTrue((self.config / 'tmp/fixture-game.resume').exists())
        self.request('install.cancel', {'taskId': task['id']})
        self.wait_for('cancelled')
        self.server.gate.set()
        task = self.prepare()
        self.request('install.start', {'taskId': task['id']})
        self.wait_for('completed')
        self.assertEqual(self.server.requests.count(paths[0]), 1, self.server.requests)

    def test_existing_unrelated_directory_is_rejected(self):
        self.directory.mkdir(parents=True)
        (self.directory / 'keep.txt').write_text('keep this')
        task = self.prepare()
        self.assertEqual(task['state'], 'failed')
        self.assertEqual(task['error']['code'], 'INSTALL_DIRECTORY_NOT_EMPTY')
        self.assertEqual((self.directory / 'keep.txt').read_text(), 'keep this')

    def test_empty_file_only_manifest_finishes_without_chunk_tasks(self):
        self.server.manifest, self.server.chunks, self.server.files = fixture_game(empty=True)
        task = self.prepare()
        self.assertEqual(task['plan']['downloadSize'], 0)
        self.request('install.start', {'taskId': task['id']})
        self.wait_for('completed')
        self.assertEqual((self.directory / 'empty.dat').read_bytes(), b'')

    def test_interrupted_update_is_not_launchable_and_can_be_resumed(self):
        task = self.prepare()
        self.request('install.start', {'taskId': task['id']})
        self.wait_for('completed')
        self.server.version = '2'
        self.server.manifest, self.server.chunks, self.server.files = fixture_game(version='2')
        self.server.slow_path = list(self.server.chunks)[-1]
        self.server.gate.clear()
        task = self.prepare()
        self.assertTrue(task['plan']['updating'])
        self.request('install.start', {'taskId': task['id']})
        deadline = time.monotonic() + 15
        while not json.loads((self.config / 'installed.json').read_text())['fixture-game']['needs_verification'] and time.monotonic() < deadline:
            time.sleep(0.1)
        self.request('install.cancel', {'taskId': task['id']})
        self.wait_for('cancelled')
        self.assertTrue(self.request('library.installed')['games'][0]['needsVerification'])
        self.server.gate.set()
        task = self.prepare()
        self.request('install.start', {'taskId': task['id']})
        self.wait_for('completed')
        installed = self.request('library.installed')['games'][0]
        self.assertFalse(installed['needsVerification'])
        self.assertEqual(installed['version'], '2')
        self.assertEqual((self.directory / 'game.exe').read_bytes(), self.server.files['game.exe'])


class InstallFileTests(unittest.TestCase):
    def test_import_hash_scan_can_be_cancelled_mid_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            payload = b'game' * 1024 * 1024
            (directory / 'game.exe').write_bytes(payload)
            file = FileManifest()
            file.filename, file.file_size, file.hash = 'game.exe', len(payload), sha1(payload).digest()
            manifest = Manifest()
            manifest.file_manifest_list = FML()
            manifest.file_manifest_list.elements = [file]
            cancel = threading.Event()
            self.assertIsNone(scan_files(manifest, directory, cancel, lambda **_: cancel.set()))
            self.assertEqual((directory / 'game.exe').read_bytes(), payload)

    def test_manifest_paths_cannot_escape_selected_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            for name in ('../outside', '/absolute', 'C:\\Windows\\file', 'data/../../outside'):
                with self.subTest(name=name):
                    file = FileManifest()
                    file.filename = name
                    manifest = Manifest()
                    manifest.file_manifest_list = FML()
                    manifest.file_manifest_list.elements = [file]
                    with self.assertRaises(ServiceError):
                        check_manifest_paths(manifest, Path(temporary))

    def test_windows_game_verification_matches_directory_case_on_linux(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / 'Data').mkdir()
            (directory / 'Data/game.exe').write_bytes(b'game')
            file = FileManifest()
            file.filename, file.file_size, file.hash = 'data/game.exe', 4, sha1(b'game').digest()
            manifest = Manifest()
            manifest.file_manifest_list = FML()
            manifest.file_manifest_list.elements = [file]
            self.assertTrue(verify_files(manifest, directory, threading.Event(), lambda **_: None,
                                         case_insensitive=True))


if __name__ == '__main__':
    unittest.main()
