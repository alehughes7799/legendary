"""Reject mismatched Python sidecars before starting a native desktop build."""

import importlib
import platform
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

target = sys.argv[1]
if not (3, 10) <= sys.version_info[:2] < (3, 14):
    sys.exit('The desktop sidecar requires Python 3.10–3.13; the build scripts use 3.12.')

expected_platform = ('linux' if '-linux-' in target else
                     'darwin' if '-apple-darwin' in target else
                     'win32' if '-windows-' in target else None)
if sys.platform != expected_platform:
    sys.exit(f'Python runs on {sys.platform}, but Rust targets {target}. Build on the target OS.')

architectures = {'AMD64': 'x86_64', 'arm64': 'aarch64', 'ARM64': 'aarch64',
                 'i386': 'i686', 'i486': 'i686', 'i586': 'i686', 'x86': 'i686'}
machine = platform.machine()
architecture = architectures.get(machine, machine)
target_architecture = target.split('-')[0]
expected_bits = 64 if target_architecture in ('x86_64', 'aarch64') else 32
if architecture != target_architecture or struct.calcsize('P') * 8 != expected_bits:
    sys.exit(f'Python ({machine}, {struct.calcsize("P") * 8}-bit) must match the Rust host {target}.')

for module in ('legendary.core', 'PyInstaller'):
    try:
        importlib.import_module(module)
    except ImportError:
        sys.exit(f'Missing Python dependency: {module}. Run the build script without skipping dependencies.')

print(f'Python {platform.python_version()} and Rust host {target} match.')
