"""One supervised installation at a time, with an explicit confirmation step."""

import json
import logging
import multiprocessing as mp
import os
import shutil
import sys
import time
from contextlib import suppress
from hashlib import sha1
from logging.handlers import QueueListener
from pathlib import Path, PurePosixPath, PureWindowsPath
from queue import Empty
from uuid import uuid4

from requests.exceptions import RequestException, SSLError

from legendary.core import LegendaryCore
from legendary.lfs.utils import clean_filename
from legendary.lfs.wine_helpers import case_insensitive_file_search
from legendary.models.exceptions import InvalidCredentialsError
from legendary.services.errors import ServiceError

TERMINAL = {'completed', 'failed', 'cancelled'}
MARKER = '.legendary-install.json'


def close_core(core):
    core.egs.session.close()
    core.egs.unauth_session.close()
    core.lgdapi.session.close()
    core.lgd._installed_lock.release()


def install_options(core, app_name, platform):
    installed = core.get_installed_game(app_name, skip_sync=True)
    game = core.get_game(app_name)
    if not game:
        raise ServiceError('GAME_NOT_FOUND', 'Refresh the library to find this game.')
    folder = game.metadata.get('customAttributes', {}).get('FolderName', {}).get('value') or app_name
    folder = clean_filename(folder).strip('. ') or clean_filename(app_name)
    return {
        'installPath': installed.install_path if installed else os.path.join(core.get_default_install_dir(platform), folder),
        'updating': bool(installed),
        'platform': installed.platform if installed else platform,
    }


def checked_path(value):
    path = Path(value).expanduser()
    if not path.is_absolute() or '\x00' in str(path):
        raise ServiceError('INSTALL_PATH_INVALID', 'Choose an absolute installation directory.')
    path = path.resolve()
    if path == Path(path.anchor) or path == Path.home():
        raise ServiceError('INSTALL_PATH_INVALID', 'Choose a dedicated game directory.')
    return path


def check_manifest_paths(manifest, directory, case_insensitive=False):
    for file in manifest.file_manifest_list.elements:
        name = file.filename.replace('\\', '/')
        if (not name or PureWindowsPath(name).drive or PurePosixPath(name).is_absolute()
                or '..' in PurePosixPath(name).parts):
            raise ServiceError('INSTALL_MANIFEST_INVALID', 'The manifest contains an invalid path.')
        target = directory / name
        if case_insensitive:
            target = Path(case_insensitive_file_search(str(target)))
        if not target.resolve().is_relative_to(directory):
            raise ServiceError('INSTALL_PATH_INVALID', 'An existing link points outside the game directory.')
        if file.symlink_target and not (target.parent / file.symlink_target).resolve().is_relative_to(directory):
            raise ServiceError('INSTALL_MANIFEST_INVALID', 'A manifest link points outside the game directory.')


def verify_files(manifest, directory, cancel, emit, case_insensitive=False):
    files = manifest.file_manifest_list.elements
    total = sum(file.file_size for file in files)
    checked = 0
    last_update = 0
    for file in files:
        if cancel.is_set():
            return False
        path = directory / file.filename
        if case_insensitive:
            path = Path(case_insensitive_file_search(str(path)))
        if file.symlink_target:
            valid = path.is_symlink() and os.readlink(path) == file.symlink_target
        else:
            digest = sha1()
            with path.open('rb') as stream:
                while chunk := stream.read(1024 * 1024):
                    if cancel.is_set():
                        return False
                    digest.update(chunk)
                    checked += len(chunk)
                    if time.monotonic() - last_update > 0.3:
                        emit(state='verifying', progress=checked / max(1, total) * 100)
                        last_update = time.monotonic()
            valid = digest.digest() == file.sha_hash and path.stat().st_size == file.file_size
        if not valid:
            raise ServiceError('INSTALL_VERIFY_FAILED', 'A downloaded file failed verification. Retry the installation.')
    return True


def run_install(params, events, start, cancel, core_factory=LegendaryCore):
    # A spawn process isolates the download manager and never writes to IPC stdout.
    sys.stdout = sys.stderr
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    core = manager = status = log_queue = listener = None
    terminal = {'state': 'cancelled'}

    def emit(**update):
        events.put(update)

    try:
        core = core_factory()
        if not core.lgd.lock_installed():
            raise ServiceError('INSTALL_LOCKED', 'Another Legendary process is changing installed games.')
        if not core.lgd.userdata:
            raise ServiceError('AUTH_REQUIRED', 'Sign in before installing games.')
        if not core.login():
            raise ServiceError('LOGIN_FAILED', 'Could not sign in to download the game.')
        if cancel.is_set():
            return
        app_name, platform = params['appName'], params['platform']
        old = core.get_installed_game(app_name, skip_sync=True)
        resuming_update = False
        if old:
            platform = old.platform
            with suppress(OSError, ValueError):
                resuming_update = json.loads((Path(old.install_path) / MARKER).read_text()) == {
                    'appName': app_name, 'platform': platform,
                }
            if old.needs_verification and not resuming_update or old.is_preloaded:
                raise ServiceError('VERIFICATION_REQUIRED', 'Verify the existing installation with the CLI first.')
        game = core.get_game(app_name)
        if not game:
            raise ServiceError('GAME_NOT_FOUND', 'This game is not in the account library.')
        if game.third_party_store or game.is_dlc:
            raise ServiceError('INSTALL_UNSUPPORTED', 'This title requires a third-party launcher or a base game.')
        if platform not in game.asset_infos:
            raise ServiceError('INSTALL_PLATFORM_UNAVAILABLE', 'This game is unavailable on the selected platform.')
        try:
            game.asset_infos[platform] = core.get_asset(app_name, platform=platform, update=True)
        except ValueError as exc:
            raise ServiceError('GAME_NOT_FOUND', 'The game is no longer available in this account.') from exc
        directory = checked_path(params['installPath'])
        if old and directory != Path(old.install_path).resolve():
            raise ServiceError('INSTALL_PATH_INVALID', 'Update games in their existing installation directory.')
        if not old and directory.exists() and any(directory.iterdir()):
            try:
                marker = json.loads((directory / MARKER).read_text())
            except (OSError, ValueError):
                marker = {}
            if marker != {'appName': app_name, 'platform': platform}:
                raise ServiceError('INSTALL_DIRECTORY_NOT_EMPTY', 'Choose an empty directory or the previous download directory.')
        status = mp.Queue()
        max_memory = core.lgd.config.getint('Legendary', 'max_memory', fallback=512)
        if sys.platform.startswith('linux') and Path('/dev/shm').is_dir():
            max_memory = min(max_memory, max(1, shutil.disk_usage('/dev/shm').free // (1024 * 1024) - 8))
        # Desktop installs download the complete base game, including all language tags.
        manager, analysis, install = core.prepare_download(
            game, base_path=str(directory.parent), game_folder=directory.name,
            platform=platform, status_q=status, max_shm=max_memory,
            dl_optimizations=True, disable_patching=resuming_update,
        )
        manager.keep_resume_file = True
        manifest_data = core.lgd.load_manifest(app_name, install.version, platform)
        manifest = core.load_manifest(manifest_data)
        check_manifest_paths(manifest, directory, case_insensitive=platform.startswith('Win'))

        def conditions():
            return core.check_installation_conditions(analysis, install, game, updating=bool(old))

        checks = conditions()
        plan = {
            'installPath': str(directory), 'version': install.version, 'platform': platform,
            'downloadSize': analysis.dl_size, 'installSize': analysis.install_size,
            'requiredSpace': max(0, analysis.disk_space_delta),
            'freeSpace': shutil.disk_usage(directory.parent).free,
            'warnings': sorted(checks.warnings), 'failures': sorted(checks.failures),
            'updating': bool(old),
        }
        emit(state='ready', plan=plan, title=game.app_title)
        deadline = time.monotonic() + 900
        while not start.wait(0.2):
            if cancel.is_set():
                return
            if time.monotonic() > deadline:
                raise ServiceError('INSTALL_PLAN_EXPIRED', 'The installation plan expired. Prepare it again.')
        if cancel.is_set():
            return
        if conditions().failures:
            raise ServiceError('INSTALL_REQUIREMENTS', 'Installation requirements are no longer satisfied.')
        directory.mkdir(parents=True, exist_ok=True)
        (directory / MARKER).write_text(json.dumps({'appName': app_name, 'platform': platform}))
        if old:
            # Interrupted updates must never launch a partially patched game.
            old.needs_verification = True
            core.lgd.set_installed_game(app_name, old)
        emit(state='downloading', progress=0)
        if manager.tasks:
            log_queue = mp.Queue()
            listener = QueueListener(log_queue, logging.StreamHandler(sys.stderr))
            listener.start()
            manager.logging_queue = log_queue
            manager.cancel_event = cancel
            manager.start()
            while manager.is_alive():
                try:
                    update = status.get(timeout=0.2)
                    emit(state='cancelling' if cancel.is_set() else 'downloading',
                         progress=update.progress, downloadSpeed=update.download_speed,
                         downloadedBytes=update.downloaded_bytes, etaSeconds=update.eta_seconds)
                except Empty:
                    pass
            manager.join()
            if cancel.is_set():
                return
            if manager.exitcode != 0:
                raise ServiceError('INSTALL_DOWNLOAD_FAILED', 'The download or file writer failed. Retry to resume.')
        emit(state='verifying', progress=0, downloadSpeed=0, etaSeconds=None)
        try:
            if not verify_files(manifest, directory, cancel, emit, case_insensitive=platform.startswith('Win')):
                return
        except (OSError, ServiceError):
            if manager.resume_file:
                with suppress(FileNotFoundError):
                    Path(manager.resume_file).unlink()
            raise ServiceError('INSTALL_VERIFY_FAILED', 'Game files could not be verified. Retry the installation.') from None
        if cancel.is_set():
            return
        # Registration is the commit point: cancellation after this point is a no-op.
        if old:
            install.save_path = old.save_path
        prerequisites = core.install_game(install)
        if manager.resume_file:
            with suppress(FileNotFoundError):
                Path(manager.resume_file).unlink()
        with suppress(FileNotFoundError):
            (directory / MARKER).unlink()
        terminal = {'state': 'completed', 'progress': 100, 'downloadSpeed': 0, 'etaSeconds': None,
                    'prerequisite': prerequisites.get('name') if prerequisites else None}
    except SSLError:
        terminal = {'state': 'failed', 'error': {'code': 'TLS_ERROR'}}
    except RequestException:
        terminal = {'state': 'failed', 'error': {'code': 'NETWORK_ERROR'}}
    except InvalidCredentialsError:
        terminal = {'state': 'failed', 'error': {'code': 'AUTH_REQUIRED'}}
    except ServiceError as exc:
        terminal = {'state': 'failed', 'error': {'code': exc.code, 'message': str(exc)}}
    except PermissionError:
        terminal = {'state': 'failed', 'error': {'code': 'INSTALL_PERMISSION_DENIED'}}
    except MemoryError:
        terminal = {'state': 'failed', 'error': {'code': 'INSTALL_MEMORY_REQUIRED'}}
    except Exception as exc:
        logging.getLogger('Install').error('Installation failed (%s).', type(exc).__name__)
        terminal = {'state': 'failed', 'error': {'code': 'INSTALL_FAILED'}}
    finally:
        if manager and manager.pid and manager.is_alive():
            cancel.set()
            manager.join(timeout=15)
            if manager.is_alive():
                manager.terminate()
                manager.join(timeout=5)
        if listener:
            listener.stop()
        for queue in (status, log_queue):
            if queue is not None:
                queue.close()
                queue.join_thread()
        if core:
            close_core(core)
        if cancel.is_set() and terminal['state'] != 'completed':
            terminal = {'state': 'cancelled', 'downloadSpeed': 0, 'etaSeconds': None}
        emit(**terminal)


class InstallTasks:
    def __init__(self):
        self.context = mp.get_context('spawn')
        self.task = self.process = self.events = self.start_event = self.cancel_event = None

    def status(self):
        if self.events:
            while True:
                try:
                    self.task.update(self.events.get_nowait())
                except Empty:
                    break
        if self.process and self.process.exitcode is not None:
            self.process.join()
            # Flush the final event before classifying an unexpected process exit.
            with suppress(Empty):
                self.task.update(self.events.get(timeout=0.1))
            if self.task['state'] not in TERMINAL:
                self.task.update(state='failed', error={'code': 'INSTALL_WORKER_EXITED'})
        return {'task': self.task}

    def prepare(self, params, operation='install'):
        self.status()
        if self.task and self.task['state'] not in TERMINAL:
            raise ServiceError('INSTALL_BUSY', 'Finish or cancel the current installation first.')
        if self.process and self.process.is_alive():
            self.process.join(timeout=2)
        if self.process and self.process.is_alive():
            raise ServiceError('INSTALL_BUSY', 'The previous installation is still stopping.')
        if self.events:
            self.events.close()
            self.events.join_thread()
        checked_path(params['installPath'])
        self.task = {'id': str(uuid4()), 'appName': params['appName'], 'title': params['appName'],
                     'operation': operation,
                     'state': 'preparing', 'progress': 0, 'downloadSpeed': 0,
                     'downloadedBytes': 0, 'etaSeconds': None, 'plan': None, 'error': None}
        self.events = self.context.Queue()
        self.start_event, self.cancel_event = self.context.Event(), self.context.Event()
        target = run_install
        if operation == 'import':
            from legendary.services.importer import run_import
            target = run_import
        self.process = self.context.Process(
            target=target, args=(params, self.events, self.start_event, self.cancel_event),
            name='LegendaryImport' if operation == 'import' else 'LegendaryInstall',
        )
        self.process.start()
        return {'task': self.task}

    def command(self, task_id, action):
        self.status()
        if not self.task or self.task['id'] != task_id:
            raise ServiceError('INSTALL_TASK_NOT_FOUND', 'This installation task no longer exists.')
        if action == 'start':
            if self.task['state'] != 'ready':
                raise ServiceError('INSTALL_NOT_READY', 'Prepare and review the installation first.')
            if self.task['plan']['failures']:
                raise ServiceError('INSTALL_REQUIREMENTS', 'Resolve the installation requirements first.')
            self.task['state'] = 'verifying' if self.task['operation'] == 'import' else 'downloading'
            self.start_event.set()
        elif self.task['state'] not in TERMINAL:
            self.task['state'] = 'cancelling'
            self.cancel_event.set()
        return {'task': self.task}

    def close(self):
        if self.process and self.process.is_alive():
            self.cancel_event.set()
            self.process.join(timeout=20)
            if self.process.is_alive():
                self.process.terminate()
                self.process.join(timeout=5)
