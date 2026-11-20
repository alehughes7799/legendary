"""Verify an existing game without downloading or modifying its files."""

import logging
import os
import stat
import sys
import time
from hashlib import sha1
from pathlib import Path

from requests.exceptions import RequestException, SSLError

from legendary.core import LegendaryCore
from legendary.lfs.wine_helpers import case_insensitive_file_search
from legendary.models.exceptions import InvalidCredentialsError
from legendary.services.errors import ServiceError
from legendary.services.install import check_manifest_paths, checked_path, close_core


def snapshot(path):
    info = path.stat()
    return (str(path.resolve()), info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns, os.readlink(path) if path.is_symlink() else None)


def scan_files(manifest, directory, cancel, emit, case_insensitive=False):
    files = manifest.file_manifest_list.elements
    if not files:
        raise ServiceError('IMPORT_MANIFEST_INVALID', 'The manifest contains no files.')
    total = sum(file.file_size + 1 for file in files)
    checked, last_update = 0, 0
    summary = {'totalFiles': len(files), 'matchedFiles': 0, 'missingFiles': 0,
               'changedFiles': 0, 'problemFiles': []}
    snapshots = {}
    for file in files:
        if cancel.is_set():
            return None
        path = directory / file.filename.replace('\\', '/')
        if case_insensitive:
            path = Path(case_insensitive_file_search(str(path)))
        before_bytes = checked
        try:
            before = snapshot(path)
            if file.symlink_target:
                valid = path.is_symlink() and os.readlink(path) == file.symlink_target
            elif not stat.S_ISREG(before[3]) or before[4] != file.file_size:
                valid = False
            else:
                digest = sha1()
                with path.open('rb') as stream:
                    while chunk := stream.read(1024 * 1024):
                        if cancel.is_set():
                            return None
                        digest.update(chunk)
                        checked += len(chunk)
                        if time.monotonic() - last_update > 0.3:
                            emit(state='verifying', progress=min(100, checked / total * 100))
                            last_update = time.monotonic()
                valid = digest.digest() == file.sha_hash
            valid = valid and before == snapshot(path)
            result = 'matchedFiles' if valid else 'changedFiles'
            if valid:
                snapshots[path] = before
        except (FileNotFoundError, NotADirectoryError):
            result = 'missingFiles'
        summary[result] += 1
        if result != 'matchedFiles' and len(summary['problemFiles']) < 5:
            summary['problemFiles'].append(file.filename)
        checked = before_bytes + file.file_size + 1
        emit_progress = time.monotonic() - last_update > 0.3
        if emit_progress:
            emit(state='verifying', progress=checked / total * 100)
            last_update = time.monotonic()
    return summary, snapshots


def run_import(params, events, start, cancel, core_factory=LegendaryCore):
    sys.stdout = sys.stderr
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    core = None
    terminal = {'state': 'cancelled'}

    def emit(**update):
        events.put(update)

    try:
        core = core_factory()
        if not core.lgd.lock_installed():
            raise ServiceError('INSTALL_LOCKED', 'Another Legendary process is changing installed games.')
        if not core.lgd.userdata:
            raise ServiceError('AUTH_REQUIRED', 'Sign in before importing games.')
        if not core.login():
            raise ServiceError('LOGIN_FAILED', 'Could not sign in to check game ownership.')
        if cancel.is_set():
            return
        app_name, platform = params['appName'], params['platform']
        directory = checked_path(params['installPath'])
        if not directory.is_dir():
            raise ServiceError('IMPORT_PATH_MISSING', 'Choose an existing game directory.')
        old = core.get_installed_game(app_name, skip_sync=True)
        if old and old.platform != platform:
            raise ServiceError('IMPORT_PLATFORM_CONFLICT', 'Use the platform of the existing installation.')
        for other in core.lgd.get_installed_list():
            if other.app_name != app_name and Path(other.install_path).resolve() == directory:
                raise ServiceError('IMPORT_DIRECTORY_IN_USE', 'This directory belongs to another installed game.')
        game = core.get_game(app_name)
        if not game:
            raise ServiceError('GAME_NOT_FOUND', 'Refresh the library to find this game.')
        if game.third_party_store or game.is_dlc:
            raise ServiceError('INSTALL_UNSUPPORTED', 'This title requires a third-party launcher or a base game.')
        if platform not in game.asset_infos:
            raise ServiceError('INSTALL_PLATFORM_UNAVAILABLE', 'This game is unavailable on the selected platform.')
        try:
            game.asset_infos[platform] = core.get_asset(app_name, platform=platform, update=True)
        except ValueError as exc:
            raise ServiceError('GAME_NOT_FOUND', 'This game is not available in the account.') from exc
        emit(title=game.app_title)
        try:
            # Reassociation can use the saved manifest of a known older version.
            saved_manifest = core.lgd.load_manifest(app_name, old.version, platform) if old else None
            manifest, install = core.import_game(game, str(directory), platform=platform,
                                                 manifest_data=saved_manifest)
        except (ValueError, KeyError, TypeError) as exc:
            raise ServiceError('IMPORT_MANIFEST_INVALID', 'The import manifest is invalid or belongs to another game.') from exc
        case_insensitive = platform.startswith('Win')
        check_manifest_paths(manifest, directory, case_insensitive=case_insensitive)
        emit(state='verifying', progress=0)
        scanned = scan_files(manifest, directory, cancel, emit, case_insensitive)
        if scanned is None:
            return
        summary, snapshots = scanned
        failures = []
        if summary['missingFiles'] or summary['changedFiles']:
            failures.append('文件不完整或与清单版本不一致，请检查游戏、平台和目录是否正确。'
                            '若需补齐文件，可通过 CLI 导入后修复；本次不会修改游戏文件。')
        warnings = []
        if install.version != game.asset_infos[platform].build_version:
            warnings.append('这是旧版本游戏。导入后可检查更新；在线启动可能需要先更新。')
        plan = {
            'installPath': str(directory), 'version': install.version, 'platform': platform,
            'downloadSize': 0, 'installSize': install.install_size, 'requiredSpace': 0,
            'freeSpace': None, 'warnings': warnings, 'failures': failures,
            'updating': bool(old), 'previousPath': old.install_path if old else None,
            'importSummary': summary,
        }
        emit(state='ready', progress=100, plan=plan)
        deadline = time.monotonic() + 900
        while not start.wait(0.2):
            if cancel.is_set():
                return
            if time.monotonic() > deadline:
                raise ServiceError('INSTALL_PLAN_EXPIRED', 'The import plan expired. Check the files again.')
        if cancel.is_set():
            return
        if failures:
            raise ServiceError('IMPORT_INCOMPLETE', 'The game files must pass verification before importing.')
        # Hashing large games happens once. Detect edits, replacements, and directory
        # remounts while the user reviews the plan, then commit only that verified set.
        check_manifest_paths(manifest, directory, case_insensitive=case_insensitive)
        for path, before in snapshots.items():
            if cancel.is_set():
                return
            try:
                unchanged = before == snapshot(path)
            except OSError:
                unchanged = False
            if not unchanged:
                raise ServiceError('IMPORT_FILES_CHANGED', 'Game files changed after verification. Check them again.')
        if cancel.is_set():
            return
        install.needs_verification = False
        if old:
            install.save_path = old.save_path
        # Only update Legendary's registry; EGL auto-export would modify game files.
        core.lgd.set_installed_game(app_name, install)
        prereq = install.prereq_info or {}
        terminal = {'state': 'completed', 'progress': 100, 'prerequisite': prereq.get('name')}
    except SSLError:
        terminal = {'state': 'failed', 'error': {'code': 'TLS_ERROR'}}
    except RequestException:
        terminal = {'state': 'failed', 'error': {'code': 'NETWORK_ERROR'}}
    except InvalidCredentialsError:
        terminal = {'state': 'failed', 'error': {'code': 'AUTH_REQUIRED'}}
    except ServiceError as exc:
        terminal = {'state': 'failed', 'error': {'code': exc.code, 'message': str(exc)}}
    except PermissionError:
        terminal = {'state': 'failed', 'error': {'code': 'IMPORT_PERMISSION_DENIED'}}
    except Exception as exc:
        logging.getLogger('Import').error('Import failed (%s).', type(exc).__name__)
        terminal = {'state': 'failed', 'error': {'code': 'IMPORT_FAILED'}}
    finally:
        if core:
            close_core(core)
        if cancel.is_set() and terminal['state'] != 'completed':
            terminal = {'state': 'cancelled'}
        emit(**terminal)
