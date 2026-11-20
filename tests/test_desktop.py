import io
import json
import os
import shlex
import socketserver
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from requests.exceptions import ConnectionError, SSLError

from legendary.bridge.__main__ import serve
from legendary.bridge.protocol import MAX_LINE_BYTES, decode_request, validate_request
from legendary.core import LegendaryCore
from legendary.models.exceptions import InvalidCredentialsError
from legendary.models.game import Game, InstalledGame, LaunchParameters
from legendary.services.desktop import DesktopService, ServiceError
from legendary.services.launch import build_launch, start_game


class ProtocolTests(unittest.TestCase):
    def exchange(self, lines, service=None):
        output = io.StringIO()
        serve(io.StringIO(lines), output, service or Mock())
        return [json.loads(line) for line in output.getvalue().splitlines()]

    def test_invalid_message_does_not_break_next_request(self):
        responses = self.exchange('[]\n{"id":"hello","method":"system.hello"}\n')
        self.assertEqual(responses[0]['error']['code'], 'INVALID_REQUEST')
        self.assertEqual(responses[1]['result']['protocolVersion'], 1)

    def test_invalid_params_keep_request_id(self):
        response = self.exchange(
            '{"id":7,"method":"game.launch","params":{"appName":"game","offline":"false"}}\n'
        )[0]
        self.assertEqual(response['id'], 7)
        self.assertEqual(response['error']['code'], 'INVALID_PARAMS')

    def test_rejects_arbitrary_methods_and_parameters(self):
        for request in (
            {'method': 'shell.exec', 'params': {'command': 'anything'}},
            {'method': 'auth.logout', 'params': {'path': '/tmp'}},
            {'method': 'auth.login', 'params': {}},
            {'method': 'library.list', 'params': {'platform': 'Other'}},
        ):
            with self.subTest(request=request), self.assertRaises(ServiceError):
                validate_request(request)

    def test_rejects_boolean_request_ids(self):
        with self.assertRaises(ServiceError):
            decode_request('{"id":true,"method":"system.hello"}')

    def test_drains_oversized_messages(self):
        replies = self.exchange(
            'x' * (MAX_LINE_BYTES + 20) + '\n{"id":2,"method":"system.hello"}\n'
        )
        self.assertEqual(len(replies), 2)
        self.assertEqual(replies[0]['error']['code'], 'INVALID_REQUEST')
        self.assertEqual(replies[1]['id'], 2)

    def test_exceptions_do_not_expose_tokens(self):
        service = Mock()
        service.call.side_effect = RuntimeError('secret-authorization-code')
        with self.assertLogs('DesktopBridge', level='ERROR') as logs:
            response = self.exchange('{"id":1,"method":"auth.status"}\n', service)[0]
        self.assertEqual(response['error']['code'], 'INTERNAL_ERROR')
        self.assertNotIn(
            'secret-authorization-code', json.dumps(response) + str(logs.output)
        )

    def test_prints_from_services_go_to_stderr(self):
        service = Mock()
        service.call.side_effect = lambda *_: print('diagnostic text')
        with patch('sys.stderr', new_callable=io.StringIO) as stderr:
            response = self.exchange('{"id":1,"method":"auth.status"}\n', service)
        self.assertEqual(len(response), 1)
        self.assertIn('diagnostic text', stderr.getvalue())


class ServiceTests(unittest.TestCase):
    def make_service(self):
        core = Mock()
        core.lgd.userdata = {
            'displayName': '玩家',
            'access_token': 'secret',
            'refresh_token': 'secret',
        }
        core.lgd.config.get.return_value = 'Windows'
        return DesktopService(core_factory=lambda: core), core

    def test_only_returns_public_account_fields(self):
        service, core = self.make_service()
        result = service.call('auth.status', {})
        self.assertEqual(result['displayName'], '玩家')
        self.assertNotIn('secret', json.dumps(result))
        core.egs.session.close.assert_called_once()
        core.egs.unauth_session.close.assert_called_once()
        core.lgd._installed_lock.release.assert_called_once()

    def test_accepts_login_json(self):
        service, core = self.make_service()
        result = service.call(
            'auth.login', {'code': '{"authorizationCode":"temporary-code"}'}
        )
        core.auth_code.assert_called_once_with('temporary-code', raise_errors=True)
        self.assertTrue(result['hasCredentials'])

    def test_login_failures_keep_their_real_cause(self):
        service, core = self.make_service()
        for failure, expected in (
            (ConnectionError('secret-authorization-code'), 'NETWORK_ERROR'),
            (SSLError('secret-authorization-code'), 'TLS_ERROR'),
            (PermissionError('secret-authorization-code'), 'CONFIG_WRITE_ERROR'),
            (
                InvalidCredentialsError(
                    'errors.com.epicgames.account.oauth.authorization_code_not_found'
                ),
                'AUTH_CODE_INVALID',
            ),
            (
                InvalidCredentialsError('errors.com.epicgames.account.oauth.invalid_client'),
                'AUTH_FAILED',
            ),
            (
                InvalidCredentialsError('errors.com.epicgames.oauth.corrective_action_required'),
                'AUTH_ACTION_REQUIRED',
            ),
        ):
            with self.subTest(expected=expected):
                core.auth_code.side_effect = failure
                output = io.StringIO()
                with patch('sys.stderr', new_callable=io.StringIO) as stderr:
                    serve(
                        io.StringIO('{"id":1,"method":"auth.login","params":{"code":"test"}}\n'),
                        output,
                        service,
                    )
                self.assertEqual(json.loads(output.getvalue())['error']['code'], expected)
                self.assertNotIn('secret-authorization-code', output.getvalue() + stderr.getvalue())

    def test_core_preserves_errors_for_desktop_and_boolean_result_for_cli(self):
        core = Mock()
        failure = ConnectionError('test network failure')
        core.egs.start_session.side_effect = failure
        core.lgd.userdata_lock = nullcontext(SimpleNamespace(data=None))
        with self.assertRaises(ConnectionError) as error:
            LegendaryCore.auth_code(core, 'test-code', raise_errors=True)
        self.assertIs(error.exception, failure)
        core.log.error.assert_not_called()
        core.lgd.userdata_lock = nullcontext(SimpleNamespace(data=None))
        self.assertFalse(LegendaryCore.auth_code(core, 'test-code'))

    def test_rejects_malformed_login_json(self):
        service, core = self.make_service()
        for code in ('{}', '{"authorizationCode":null}', '{'):
            with self.subTest(code=code), self.assertRaises(ServiceError):
                service.call('auth.login', {'code': code})
        core.auth_code.assert_not_called()

    def test_installed_library_does_not_sync_egl(self):
        service, core = self.make_service()
        core.lgd.get_installed_list.return_value = [
            InstalledGame('game', '/games/game', 'Game', '1'),
            InstalledGame('dlc', '/games/game', 'DLC', '1', is_dlc=True),
        ]
        result = service.call('library.installed', {})
        self.assertEqual([game['appName'] for game in result['games']], ['game'])
        core.egl_sync.assert_not_called()
        core.login.assert_not_called()

    def test_library_is_explicitly_serialized(self):
        service, core = self.make_service()
        game = Game(
            'game',
            'A Game',
            metadata={
                'keyImages': [
                    {
                        'type': 'DieselGameBoxTall',
                        'url': 'https://cdn.epicgames.com/game.jpg',
                    },
                ],
                'private-data': 'do-not-return',
            },
        )
        core.get_game_and_dlc_list.return_value = ([game], {})
        result = service.call('library.list', {'platform': 'Windows'})
        self.assertEqual(
            result['games'][0]['coverUrl'], 'https://cdn.epicgames.com/game.jpg'
        )
        self.assertNotIn('do-not-return', json.dumps(result))

    def test_cannot_launch_unverified_install(self):
        service, core = self.make_service()
        core.get_installed_game.return_value = InstalledGame(
            'game',
            '/games',
            'Game',
            '1',
            executable='game.exe',
            needs_verification=True,
        )
        with (
            patch('legendary.services.desktop.start_game') as start,
            self.assertRaises(ServiceError) as error,
        ):
            service.call('game.launch', {'appName': 'game'})
        self.assertEqual(error.exception.code, 'VERIFICATION_REQUIRED')
        start.assert_not_called()

    def test_online_launch_requires_current_version(self):
        service, core = self.make_service()
        with tempfile.TemporaryDirectory() as directory:
            core.get_installed_game.return_value = InstalledGame(
                'game', directory, 'Game', 'old', executable='game.exe'
            )
            core.is_offline_game.return_value = False
            core.is_noupdate_game.return_value = False
            core.get_asset.return_value = SimpleNamespace(build_version='new')
            with (
                patch('legendary.services.desktop.start_game') as start,
                self.assertRaises(ServiceError) as error,
            ):
                service.call('game.launch', {'appName': 'game'})
        self.assertEqual(error.exception.code, 'UPDATE_REQUIRED')
        start.assert_not_called()


class LaunchTests(unittest.TestCase):
    def test_frozen_runtime_does_not_override_game_libraries(self):
        with (
            patch.object(sys, 'frozen', True, create=True),
            patch.dict(
                os.environ,
                {
                    'LD_LIBRARY_PATH': '/tmp/pyinstaller',
                    'LD_LIBRARY_PATH_ORIG': '/system/libraries',
                    'DYLD_LIBRARY_PATH': '/tmp/pyinstaller',
                },
                clear=True,
            ),
        ):
            _, environment = build_launch(LaunchParameters())
        self.assertEqual(environment['LD_LIBRARY_PATH'], '/system/libraries')
        self.assertNotIn('DYLD_LIBRARY_PATH', environment)
        self.assertEqual(environment['PYINSTALLER_RESET_ENVIRONMENT'], '1')

    def test_preserves_argument_boundaries_and_parent_environment(self):
        params = LaunchParameters(
            game_directory='/games/with spaces',
            game_executable='game.exe',
            launch_command=['wine'],
            game_parameters=['--game'],
            user_parameters=['a b', '$(echo unsafe)'],
            egl_parameters=['-AUTH_PASSWORD=secret'],
            environment={'GAME_ENV': 'value'},
        )
        with patch.dict(os.environ, {'EXISTING_ENV': 'keep'}):
            command, environment = build_launch(params)
        self.assertEqual(
            command,
            [
                'wine',
                '/games/with spaces/game.exe',
                '--game',
                'a b',
                '$(echo unsafe)',
                '-AUTH_PASSWORD=secret',
            ],
        )
        self.assertEqual(environment['EXISTING_ENV'], 'keep')
        self.assertEqual(environment['GAME_ENV'], 'value')
        self.assertNotIn('GAME_ENV', os.environ)

    def test_detached_game_runs_without_inheriting_protocol_pipes(self):
        with tempfile.TemporaryDirectory(prefix='legendary game ') as directory:
            output = Path(directory, 'result.json')
            params = LaunchParameters(
                game_directory=str(Path(sys.executable).parent),
                game_executable=Path(sys.executable).name,
                working_directory=directory,
                environment={'LEGENDARY_TEST_VALUE': 'kept'},
                user_parameters=[
                    '-c',
                    'import os,json,sys; print("game output"); open("result.json","w").write(json.dumps([sys.argv[1],os.environ["LEGENDARY_TEST_VALUE"],os.getcwd()]))',
                    'argument with spaces; $(echo no)',
                ],
            )
            process = start_game(params, detached=True)
            self.assertEqual(process.wait(timeout=10), 0)
            self.assertEqual(
                json.loads(output.read_text()),
                ['argument with spaces; $(echo no)', 'kept', directory],
            )


class ProcessTests(unittest.TestCase):
    @staticmethod
    def bridge_command():
        if binary := os.environ.get('LEGENDARY_TEST_BRIDGE'):
            return [binary]
        return [sys.executable, '-u', '-m', 'legendary.bridge']

    def test_bundled_https_login_and_certificate_verification(self):
        # These fixtures are public test credentials, used only by a local TLS server.
        fixtures = Path(__file__).parent / 'fixtures'
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(fixtures / 'epic-test-cert.pem', fixtures / 'epic-test-key.pem')
        requests = []

        class LoginProxy(socketserver.BaseRequestHandler):
            def handle(self):
                self.request.settimeout(5)
                headers = b''
                while b'\r\n\r\n' not in headers and len(headers) < 8192:
                    data = self.request.recv(4096)
                    if not data:
                        return
                    headers += data
                self.request.sendall(b'HTTP/1.1 200 Connection established\r\n\r\n')
                try:
                    with context.wrap_socket(self.request, server_side=True) as connection:
                        data = b''
                        while b'\r\n\r\n' not in data:
                            data += connection.recv(4096)
                        headers, body = data.split(b'\r\n\r\n', 1)
                        length = next(
                            int(line.split(b':', 1)[1])
                            for line in headers.split(b'\r\n')
                            if line.lower().startswith(b'content-length:')
                        )
                        while len(body) < length:
                            body += connection.recv(4096)
                        requests.append(body.decode())
                        payload = json.dumps({
                            'access_token': 'test-access-token',
                            'refresh_token': 'test-refresh-token',
                            'account_id': 'test-account',
                            'displayName': 'TLS Test Player',
                        }).encode()
                        connection.sendall(
                            b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n'
                            + f'Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n'.encode()
                            + payload
                        )
                except ssl.SSLError:
                    pass  # Expected when the client rejects our untrusted test certificate.

        with socketserver.ThreadingTCPServer(('127.0.0.1', 0), LoginProxy) as server:
            server.daemon_threads = True
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                for trust_certificate in (True, False):
                    with self.subTest(trusted=trust_certificate), tempfile.TemporaryDirectory() as directory:
                        environment = {
                            key: value for key, value in os.environ.items()
                            if key.lower() not in {
                                'https_proxy', 'http_proxy', 'all_proxy', 'no_proxy',
                                'requests_ca_bundle', 'curl_ca_bundle',
                            }
                        }
                        environment.update(
                            LEGENDARY_CONFIG_PATH=directory,
                            HTTPS_PROXY=f'http://127.0.0.1:{server.server_address[1]}',
                            NO_PROXY='',
                        )
                        if trust_certificate:
                            environment['REQUESTS_CA_BUNDLE'] = str(fixtures / 'epic-test-cert.pem')
                        result = subprocess.run(
                            self.bridge_command(),
                            input='{"id":1,"method":"auth.login","params":{"code":"local-test-code"}}\n',
                            capture_output=True, text=True, encoding='utf-8',
                            env=environment, timeout=15,
                        )
                        self.assertEqual(result.returncode, 0, result.stderr)
                        reply = json.loads(result.stdout)
                        if trust_certificate:
                            self.assertTrue(reply['result']['hasCredentials'], result.stdout)
                            self.assertEqual(reply['result']['displayName'], 'TLS Test Player')
                            saved = json.loads(Path(directory, 'user.json').read_text())
                            self.assertEqual(saved['refresh_token'], 'test-refresh-token')
                        else:
                            self.assertEqual(reply['error']['code'], 'TLS_ERROR', result.stdout)
                            self.assertFalse(Path(directory, 'user.json').exists())
                        self.assertNotIn('test-access-token', result.stdout + result.stderr)
                        self.assertNotIn('test-refresh-token', result.stdout + result.stderr)
                self.assertEqual(requests, ['grant_type=authorization_code&code=local-test-code&token_type=eg1'])
            finally:
                server.shutdown()
                thread.join(timeout=5)

    def test_real_bridge_observes_cli_file_updates(self):
        with tempfile.TemporaryDirectory() as directory:
            environment = dict(os.environ, LEGENDARY_CONFIG_PATH=directory)
            process = subprocess.Popen(
                self.bridge_command(),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding='utf-8',
                env=environment,
            )

            def request(request_id, method):
                process.stdin.write(
                    json.dumps({'id': request_id, 'method': method}) + '\n'
                )
                process.stdin.flush()
                return json.loads(process.stdout.readline())

            try:
                self.assertEqual(
                    request(1, 'system.hello')['result']['protocolVersion'], 1
                )
                self.assertFalse(request(2, 'auth.status')['result']['hasCredentials'])
                self.assertEqual(request(3, 'library.installed')['result']['games'], [])
                Path(directory, 'installed.json').write_text(
                    json.dumps(
                        {
                            'example': {
                                'app_name': 'example',
                                'title': 'CLI 游戏',
                                'version': '1',
                                'install_path': '/games/example',
                            },
                        }
                    )
                )
                self.assertEqual(
                    request(4, 'library.installed')['result']['games'][0]['title'],
                    'CLI 游戏',
                )
                process.stdin.close()
                self.assertEqual(process.wait(timeout=10), 0)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                process.stdout.close()
                process.stderr.close()

    def test_offline_game_survives_bridge_exit_and_keeps_output_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory)
            (config / 'metadata').mkdir()
            output = config / 'game-output.json'
            script = (
                'import json,os,time; print("game stdout"); time.sleep(0.3); '
                f'open({str(output)!r},"w").write(json.dumps([os.environ.get("LD_LIBRARY_PATH"),"finished"]))'
            )
            (config / 'config.ini').write_text(
                '[Legendary]\ndisable_update_check = true\n[game]\nstart_params = '
                + shlex.join(['-c', script])
                + '\n',
                encoding='utf-8',
            )
            (config / 'user.json').write_text(
                json.dumps({'account_id': 'test-account', 'displayName': 'Test Player'})
            )
            (config / 'installed.json').write_text(
                json.dumps(
                    {
                        'game': {
                            'app_name': 'game',
                            'title': 'Test game',
                            'version': '1',
                            'install_path': str(Path(sys.executable).parent),
                            'executable': Path(sys.executable).name,
                            'platform': 'Mac',
                            'can_run_offline': True,
                        }
                    }
                )
            )
            (config / 'metadata' / 'game.json').write_text(
                json.dumps(
                    {
                        'app_name': 'game',
                        'app_title': 'Test game',
                        'metadata': {'id': 'test-game', 'namespace': 'test-namespace'},
                    }
                )
            )
            result = subprocess.run(
                self.bridge_command(),
                text=True,
                encoding='utf-8',
                capture_output=True,
                input=json.dumps(
                    {
                        'id': 1,
                        'method': 'game.launch',
                        'params': {'appName': 'game', 'offline': True},
                    }
                )
                + '\n',
                env=dict(os.environ, LEGENDARY_CONFIG_PATH=directory),
                timeout=15,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            reply = json.loads(result.stdout)
            self.assertIn('pid', reply.get('result', {}), result.stdout)
            self.assertNotIn('game stdout', result.stdout)
            deadline = time.monotonic() + 5
            while not output.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(
                output.exists(), 'The detached game did not finish after bridge exit'
            )
            libraries, state = json.loads(output.read_text())
            self.assertEqual(state, 'finished')
            self.assertNotIn('_MEI', libraries or '')


if __name__ == '__main__':
    unittest.main()
