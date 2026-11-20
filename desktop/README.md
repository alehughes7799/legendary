# Legendary Desktop

Tauri 2 + React/TypeScript frontend with a bundled Python sidecar. Supports Epic
authorization-code login/logout, library browsing, base-game installation and
updates, importing existing games, installed games, and online/offline launching. Repair, selective language
downloads, DLC installation, cloud saves, and third-party launcher activation
remain CLI operations.

## Installing games

Select a game, choose its dedicated installation directory, and check the plan.
The plan shows download/install sizes, required/free space, warnings, and blocking
conditions. Downloads start only after confirmation. The download panel shows
progress, transfer speed, and estimated remaining time; it remains available
while browsing the app. Installed games also offer an update/resume action.

One installation runs at a time. Cancel to retain completed files, then prepare
the same game in the same directory to resume. Exiting the app interrupts the
download; tasks do not continue in the background. A fresh install requires an
empty directory or a directory marked by a previous desktop download. Full base
games, including all language tags, are downloaded; prerequisites are reported
after installation and must be installed separately if needed.

## Importing existing games

After reinstalling the OS, sign in with the owning Epic account and refresh the
library. Select the game, choose **导入已有游戏**, and select its existing game
root directory. **检查已有文件** reads the manifest and checks every file's size
and SHA-1, with cancellable progress. The result lists matching, missing, and
changed files. Confirm **确认导入** to restore the installation record; importing
does not download, overwrite, or delete game files or unrelated files such as saves.

Recorded installations offer **重新关联目录** for moving a game or reconnecting a
disk at a different path. The previous record stays intact until confirmation,
and its configured save location is retained. Games can be launched and updated
through the normal actions after importing. External runtime prerequisites and
Wine configuration may still need to be restored after an OS reinstall.

Import uses a saved manifest when reassociating a recorded installation, or a
matching Windows Epic `.egstore` manifest when available, otherwise the current
owned version's manifest. Older files without a matching manifest, modified files,
and installations missing optional language files may fail verification. In that
case registration is blocked; use the CLI `legendary import <AppName> <directory>`
followed by `legendary repair <AppName>` to repair, then recheck in the desktop app.
Desktop import supports one base game at a time, rather than automatic disk scans.

## Development

Use Node.js 22+, pnpm 10, stable Rust, and Python 3.10–3.13. Install the
[Tauri system prerequisites](https://v2.tauri.app/start/prerequisites/).
On Debian/Ubuntu Linux these include a C toolchain, pkg-config,
`libwebkit2gtk-4.1-dev`, `libayatana-appindicator3-dev`, `librsvg2-dev`,
and `patchelf`. Python 3.14 currently needs a separate core compatibility fix
because `locale.getdefaultlocale` was removed.

From the repository root:

```sh
uv sync --python 3.12 --extra pyinstaller_build
cd desktop
pnpm install --frozen-lockfile
pnpm desktop:dev
```

`desktop:dev` builds the sidecar, then starts Vite and Tauri. The build script
uses the root `.venv`, or `LEGENDARY_DESKTOP_PYTHON` when supplied. In debug
builds this variable also lets Tauri execute `python -m legendary.bridge`
directly for Python development. Release builds always use the bundled binary.

`pnpm dev` provides a browser-only UI preview; real account and game operations
require the desktop application. It does not substitute sample account data.

## Validation

```sh
# Repository root, with the Python environment active
python -m unittest discover -s tests -v
ruff check legendary desktop/sidecar_entry.py desktop/pyinstaller-hooks tests

# desktop/
pnpm test
pnpm build
cargo test --manifest-path src-tauri/Cargo.toml -p legendary-transport
pnpm desktop:build
```

The transport tests need Python on PATH (`python3`, or set
`LEGENDARY_TEST_PYTHON` to its absolute executable path). They run independently
of GTK/WebView libraries. The application itself requires those native libraries.

## Packaging

Native build scripts for Linux, macOS, and Windows, including prerequisites,
commands, and output locations, are documented in [BUILDING.md](BUILDING.md).

`pnpm desktop:build` creates a PyInstaller onefile executable and adds it to
Tauri's `bundle.externalBin`. Sidecar filenames include the Rust host target
triple. Build separately on each target OS and architecture; there is no Python
cross-compilation. For example, `pnpm sidecar:build --target
x86_64-unknown-linux-gnu` can select the filename explicitly on a Linux x64 host.
Use a sufficiently old Linux build environment for the required glibc baseline.

The sidecar build includes optional TLS policy data required by customized Python
distributions. The process tests exercise HTTPS login through a local TLS server
with public test credentials, both with a trusted certificate and with an
untrusted certificate. Run these tests against the frozen executable as well;
an IPC-only startup test does not verify that HTTPS works in a packaged runtime.

Installers need no user-installed Python. Linux still needs the distribution's
WebKitGTK runtime, and Windows needs WebView2 as managed by the Tauri installer.
Signing, notarization, and automatic updates require release configuration.

## Runtime contract

- UTF-8 JSON Lines on stdin/stdout, with protocol version 1 checked at startup.
  Each request carries an id; each reply contains either `result` or
  `error: { code, message }`. No shell commands are exposed to the frontend.
- Methods: `system.hello`, `auth.status`, `auth.login`, `auth.logout`,
  `library.installed`, `library.list`, `game.launch`, `install.options`,
  `install.prepare`, `import.prepare`, `install.status`, `install.start`, and `install.cancel`.
  Arguments are validated against a fixed schema before entering the service layer.
- Requests execute serially in Python. Rust correlates replies, bounds pending
  requests and response size, drains stderr, and fails waiting requests when the
  sidecar disconnects. A later request reconnects; failed operations are never
  replayed automatically. Requests time out after ten minutes (large libraries
  can require many Epic requests).
- Installation analysis/downloads and import verification run in a separate spawn process, leaving the
  bridge available for progress polling and cancellation. An installed-data lock
  excludes CLI writers from preparation through final registration. Unconfirmed
  plans expire after 15 minutes; task history is in memory, while resume data and
  directory markers survive application restarts. Remote library refresh is
  disabled during active installations to keep control requests responsive.
- Writer errors, unexpected worker exits, and exhausted retries fail downloads.
  Every installed file is checked against its manifest SHA-1 before registration.
  Interrupted updates remain marked as requiring verification until a successful
  resumed install. Installed metadata is replaced atomically. Cancellation cannot
  roll back a successfully verified installation once registration has started.
- Imports share the installation queue and lock. Scans leave game directories
  unchanged and require confirmation before registration. File identities and
  timestamps are checked again at confirmation; changes invalidate the plan.
  Import does not trigger automatic Epic Launcher export.
- Each operation creates a fresh core instance to observe external CLI edits.
  Desktop reads avoid implicit EGL synchronization and config writes.
  `LEGENDARY_CONFIG_PATH` and the existing XDG configuration rules still apply.
- Login uses `https://legendary.gl/epiclogin` in the system browser. Paste the
  returned authorization code or JSON. Access/refresh tokens and game launch
  arguments are never returned to the frontend.
- Games use the same launch helper as the CLI. Desktop launches redirect game
  streams, restore system library paths from the frozen Python runtime, and
  start a separate process session. Exiting the UI stops the sidecar
  process tree while launched games continue running.
- Desktop launch currently targets installed base games. CLI workflows for
  launchable add-ons, third-party activation, and interactive CrossOver setup
  remain available. Existing Wine/CrossOver configuration is respected.

## Initial validation

The implementation has 43 Python tests, 17 frontend tests, and 4 Rust
transport tests. Python process tests can also run against the frozen executable
by setting `LEGENDARY_TEST_BRIDGE` to its absolute path. They cover IPC framing,
external CLI state changes, and an isolated native test process that continues
after the bridge exits. Linux native compilation and desktop-to-sidecar startup
have also been checked. Online login and launching a real Epic title still need
an account-based acceptance run; Windows/macOS builds are defined in CI.

Installation tests serve a synthetic Epic game through a local HTTPS fixture and
exercise the real bridge, manifests, chunk downloads, writer, verification, and
registration. They cover confirmation, empty-file manifests, cancellation/resume,
interrupted updates, unrelated directories, writer failure, and corrupt files.
No real account or purchased game is needed; large real-title installation still
needs an account-based acceptance run.

Import process tests cover complete files without chunk downloads, missing and
corrupt files, cancellation, file changes during review, directory reassociation,
old Epic manifests, platform selection, invalid paths, and exclusion of concurrent
installations. These tests also run against the bundled Python executable.
