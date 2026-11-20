"""Keep optional TLS policy data used by customized Python distributions."""

from pathlib import Path

from PyInstaller.utils.hooks import get_module_file_attribute

# Some Python distributions load this distrust policy relative to ssl.__file__
# whenever SSLContext is created. Preserve that policy in the frozen runtime.
policy = Path(get_module_file_attribute('ssl')).with_name('codex-rejected-cas.pem')
datas = [(str(policy), '.')] if policy.is_file() else []
