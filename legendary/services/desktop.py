"""Small, explicitly serialized API for the desktop application."""

import json
import os
import threading
from sys import platform as sys_platform

from requests.exceptions import RequestException

from legendary.core import LegendaryCore
from legendary.models.exceptions import InvalidCredentialsError
from legendary.services.errors import ServiceError
from legendary.services.install import InstallTasks, close_core, install_options
from legendary.services.launch import start_game


def auth_status(core):
    user = core.lgd.userdata or {}
    return {
        'hasCredentials': bool(user),
        'displayName': user.get('displayName'),
        'defaultPlatform': core.lgd.config.get(
            'Legendary',
            'default_platform',
            fallback='Mac' if sys_platform == 'darwin' else 'Windows',
        ),
    }


def installed_summary(game):
    return {
        'appName': game.app_name,
        'title': game.title,
        'version': game.version,
        'platform': game.platform,
        'installPath': game.install_path,
        'installSize': game.install_size,
        'canRunOffline': game.can_run_offline,
        'needsVerification': game.needs_verification,
        'executable': bool(game.executable),
    }


class DesktopService:
    def __init__(self, core_factory=LegendaryCore):
        self.core_factory = core_factory
        self.installs = InstallTasks()

    def close(self):
        self.installs.close()

    @staticmethod
    def require_login(core):
        if not core.lgd.userdata:
            raise ServiceError(
                'AUTH_REQUIRED', 'Please sign in to your Epic Games account.'
            )
        try:
            success = core.login()
        except (InvalidCredentialsError, ValueError) as exc:
            raise ServiceError(
                'AUTH_REQUIRED', 'Your saved session has expired. Please sign in again.'
            ) from exc
        if not success:
            raise ServiceError(
                'LOGIN_FAILED',
                'Could not sign in. Check your connection or sign in again.',
            )

    def call(self, method: str, params: dict):
        if method in ('install.prepare', 'import.prepare'):
            return self.installs.prepare(params, operation='import' if method == 'import.prepare' else 'install')
        if method == 'install.status':
            return self.installs.status()
        if method in ('install.start', 'install.cancel'):
            return self.installs.command(params['taskId'], method.split('.')[1])
        # Fresh instances see CLI edits and release any file locks at request end.
        core = self.core_factory()
        try:
            if method == 'auth.status':
                return auth_status(core)
            if method == 'install.options':
                return install_options(core, params['appName'], params['platform'])
            if method == 'auth.login':
                code = params['code'].strip()
                if code.startswith('{'):
                    try:
                        code = json.loads(code)['authorizationCode']
                    except (ValueError, KeyError, TypeError) as exc:
                        raise ServiceError(
                            'INVALID_PARAMS',
                            'Paste an authorization code or the login JSON response.',
                        ) from exc
                if not isinstance(code, str) or not code.strip():
                    raise ServiceError(
                        'INVALID_PARAMS', 'The authorization code is empty.'
                    )
                try:
                    success = core.auth_code(code.strip().strip('"'), raise_errors=True)
                except InvalidCredentialsError as exc:
                    reason = str(exc)
                    if reason in {
                        'errors.com.epicgames.account.oauth.authorization_code_not_found',
                        'errors.com.epicgames.account.oauth.expired_authorization_code',
                        'errors.com.epicgames.account.oauth.invalid_grant',
                    }:
                        raise ServiceError(
                            'AUTH_CODE_INVALID',
                            'Epic rejected this authorization code. Get a new code and retry.',
                        ) from exc
                    if reason == 'errors.com.epicgames.oauth.corrective_action_required':
                        raise ServiceError(
                            'AUTH_ACTION_REQUIRED',
                            'Complete any pending account steps on the Epic Games website.',
                        ) from exc
                    raise ServiceError(
                        'AUTH_FAILED', 'Epic Games rejected the sign-in request.'
                    ) from exc
                except RequestException:
                    raise
                except OSError as exc:
                    raise ServiceError(
                        'CONFIG_WRITE_ERROR',
                        'Could not read or save login credentials. Check configuration permissions.',
                    ) from exc
                if not success:
                    raise ServiceError(
                        'LOGIN_FAILED', 'Could not complete sign-in.',
                    )
                return auth_status(core)
            if method == 'auth.logout':
                core.lgd.invalidate_userdata()
                return auth_status(core)
            if method == 'library.installed':
                # Reading the desktop library must not trigger an EGL sync/write.
                games = core.lgd.get_installed_list()
                return {
                    'games': [
                        installed_summary(g)
                        for g in sorted(games, key=lambda g: g.title.casefold())
                        if not g.is_dlc
                    ]
                }
            if method == 'library.list':
                self.require_login(core)
                platform = params.get('platform', auth_status(core)['defaultPlatform'])
                games, _ = core.get_game_and_dlc_list(
                    platform=platform,
                    force_refresh=params.get('forceRefresh', False),
                    skip_ue=True,
                )
                result = []
                for game in sorted(games, key=lambda g: g.app_title.casefold()):
                    images = (game.metadata or {}).get('keyImages') or []
                    cover = next(
                        (
                            image.get('url')
                            for kind in (
                                'DieselGameBoxTall',
                                'OfferImageTall',
                                'Thumbnail',
                            )
                            for image in images
                            if image.get('type') == kind
                            and isinstance(image.get('url'), str)
                            and image['url'].startswith('https://')
                        ),
                        None,
                    )
                    result.append(
                        {
                            'appName': game.app_name,
                            'title': game.app_title,
                            'version': game.app_version(platform),
                            'platform': platform,
                            'coverUrl': cover,
                        }
                    )
                return {'games': result}
            if method == 'game.launch':
                return self.launch(core, params)
            raise ServiceError('METHOD_NOT_FOUND', 'Unknown desktop method.')
        finally:
            # Do not write config defaults on read operations or overwrite CLI edits.
            close_core(core)

    def launch(self, core, params):
        app_name = params['appName']
        game = core.get_installed_game(app_name, skip_sync=True)
        if not game:
            raise ServiceError('NOT_INSTALLED', 'This game is not installed.')
        if not game.executable:
            raise ServiceError(
                'NOT_LAUNCHABLE',
                'This title has no executable. Launch its base game instead.',
            )
        if game.needs_verification or game.is_preloaded:
            raise ServiceError(
                'VERIFICATION_REQUIRED',
                'Verify or finish installing this game with the CLI first.',
            )
        if not os.path.isdir(game.install_path):
            raise ServiceError(
                'INSTALL_MISSING', 'The game installation directory is missing.'
            )
        if core.get_game(app_name) is None:
            raise ServiceError(
                'METADATA_MISSING',
                'Refresh the game library before launching this game.',
            )
        if not core.lgd.userdata:
            raise ServiceError(
                'AUTH_REQUIRED',
                'Sign in once before launching this game, including offline mode.',
            )

        offline = params.get('offline', False) or core.is_offline_game(app_name)
        if not offline:
            self.require_login(core)
            if not params.get('skipVersionCheck', False) and not core.is_noupdate_game(
                app_name
            ):
                latest = core.get_asset(app_name, update=True, platform=game.platform)
                if latest.build_version != game.version:
                    raise ServiceError(
                        'UPDATE_REQUIRED',
                        'Update this game with the CLI or enable skipping the version check.',
                    )

        launch = core.get_launch_parameters(app_name=app_name, offline=offline)
        process = start_game(launch, detached=True)
        # Reap children that exit while this persistent sidecar is still running.
        threading.Thread(target=process.wait, daemon=True).start()
        return {'appName': app_name, 'pid': process.pid}
