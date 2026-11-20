# Shared by build-linux.sh and build-macos.sh; compatible with macOS Bash 3.2.
build_desktop() {
    local expected_os="$1" bundles="$2"
    shift 2
    local skip_dependencies=false argument tool rust_info target python bundle_directory
    for argument in "$@"; do
        case "$argument" in
            --skip-deps) skip_dependencies=true ;;
            -h|--help)
                cat <<'HELP'
Usage: bash desktop/scripts/build-{linux,macos}.sh [--skip-deps]

Run on the matching OS. Requires Node.js 22+, pnpm 10, uv 0.11+,
stable Rust, and the native Tauri prerequisites (see desktop/BUILDING.md).
By default, installs locked dependencies into .venv and desktop/node_modules.
--skip-deps reuses installed dependencies and LEGENDARY_DESKTOP_PYTHON if set.
HELP
                return 0
                ;;
            *) printf 'Unknown option: %s\n' "$argument" >&2; return 1 ;;
        esac
    done
    if [[ "$(uname -s)" != "$expected_os" ]]; then
        printf 'Run this script on %s; cross-compiling the Python sidecar is unsupported.\n' "$expected_os" >&2
        return 1
    fi
    cd -- "$script_dir/../.."
    for tool in node pnpm cargo rustc; do
        if ! command -v "$tool" >/dev/null 2>&1; then
            printf 'Missing build tool: %s. See desktop/BUILDING.md.\n' "$tool" >&2
            return 1
        fi
    done
    node -e 'if (Number(process.versions.node.split(".")[0]) < 22) { console.error("Node.js 22+ is required."); process.exit(1); }'
    rust_info="$(rustc -vV)"
    target="$(printf '%s\n' "$rust_info" | sed -n 's/^host: //p')"
    case "$expected_os:$target" in
        Linux:*-unknown-linux-gnu|Darwin:*-apple-darwin) ;;
        *) printf 'Unsupported native Rust toolchain: %s\n' "$target" >&2; return 1 ;;
    esac
    if [[ "$expected_os" == Linux ]]; then
        for tool in cc "${PKG_CONFIG:-pkg-config}" patchelf; do
            if ! command -v "$tool" >/dev/null 2>&1; then
                printf 'Missing Linux prerequisite: %s. See desktop/BUILDING.md.\n' "$tool" >&2
                return 1
            fi
        done
        if ! "${PKG_CONFIG:-pkg-config}" --exists gtk+-3.0 webkit2gtk-4.1; then
            printf 'GTK 3 / WebKitGTK 4.1 development libraries are missing. See desktop/BUILDING.md.\n' >&2
            return 1
        fi
    else
        xcrun --find clang >/dev/null
    fi
    if [[ "$skip_dependencies" == false ]]; then
        if ! command -v uv >/dev/null 2>&1; then
            printf 'Missing build tool: uv 0.11+. See desktop/BUILDING.md.\n' >&2
            return 1
        fi
        uv sync --locked --python 3.12 --extra pyinstaller_build
        python="$PWD/.venv/bin/python"
        (cd desktop && pnpm install --frozen-lockfile)
    else
        python="${LEGENDARY_DESKTOP_PYTHON:-$PWD/.venv/bin/python}"
    fi
    if [[ ! -x "$python" ]]; then
        printf 'Python environment not found: %s. Run without --skip-deps first.\n' "$python" >&2
        return 1
    fi
    "$python" desktop/scripts/check-build-env.py "$target"
    export LEGENDARY_DESKTOP_PYTHON="$python"
    cd desktop
    pnpm sidecar:build --target "$target"
    pnpm tauri build --ci --target "$target" --bundles "$bundles" -- --locked
    bundle_directory="${CARGO_TARGET_DIR:-$PWD/src-tauri/target}"
    printf '\nBuild complete (%s). Bundles: %s/%s/release/bundle/\n' "$target" "$bundle_directory" "$target"
}
