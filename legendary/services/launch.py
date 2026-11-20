"""Launch prepared games without tying process management to a user interface."""

import logging
import os
import shlex
import subprocess
import sys

from legendary.lfs.crossover import mac_is_valid_bottle
from legendary.models.game import LaunchParameters

logger = logging.getLogger('LaunchService')


def build_launch(params: LaunchParameters) -> tuple[list[str], dict[str, str]]:
    environment = os.environ.copy()
    if getattr(sys, 'frozen', False):
        # PyInstaller adjusts shared-library search paths for its own runtime.
        # External games and Wine need the original system paths instead.
        for key in ('LD_LIBRARY_PATH', 'LIBPATH', 'DYLD_LIBRARY_PATH'):
            original = environment.get(f'{key}_ORIG')
            if original is not None:
                environment[key] = original
            else:
                environment.pop(key, None)
        environment['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
    environment.update(params.environment)
    command = list(params.launch_command)
    if 'LEGENDARY_WRAPPER_EXE' in environment:
        command.append(environment['LEGENDARY_WRAPPER_EXE'].strip())
    command.append(os.path.join(params.game_directory, params.game_executable))
    command.extend(params.game_parameters)
    command.extend(params.user_parameters)
    command.extend(params.egl_parameters)

    if 'CX_BOTTLE' in environment and any(
        'SharedSupport/CrossOver' in p for p in params.launch_command
    ):
        environment.pop('WINEPREFIX', None)
        if not mac_is_valid_bottle(environment['CX_BOTTLE'].strip()):
            raise ValueError('The configured CrossOver bottle does not exist.')
    return command, environment


def start_game(params: LaunchParameters, *, detached: bool = False) -> subprocess.Popen:
    command, environment = build_launch(params)
    options = {}
    if detached:
        # Games must neither inherit the bridge pipes nor die with the launcher.
        options.update(
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if os.name == 'nt':
            options['creationflags'] = (
                subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
            )
        else:
            options['start_new_session'] = True

    frozen_windows = os.name == 'nt' and getattr(sys, 'frozen', False)
    if frozen_windows:
        import ctypes

        ctypes.windll.kernel32.SetDllDirectoryW(None)
    try:
        if params.pre_launch_command:
            try:
                pre_launch = subprocess.Popen(
                    shlex.split(params.pre_launch_command), env=environment, **options
                )
                if params.pre_launch_wait:
                    pre_launch.wait()
            except (OSError, ValueError):
                logger.warning(
                    'The configured pre-launch command could not be started.'
                )

        # Preserve the CLI's Windows invocation; desktop launches use direct argv.
        return subprocess.Popen(
            command,
            cwd=params.working_directory,
            env=environment,
            shell=os.name == 'nt' and not detached,
            **options,
        )
    finally:
        if frozen_windows:
            ctypes.windll.kernel32.SetDllDirectoryW(sys._MEIPASS)
