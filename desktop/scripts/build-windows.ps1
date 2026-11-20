param(
    [switch]$SkipDependencies,
    [switch]$Help
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if ($Help) {
    Write-Output @'
Usage: powershell -File desktop/scripts/build-windows.ps1 [-SkipDependencies]

Run on Windows with the MSVC Rust toolchain and Visual Studio C++ build tools.
Requires Node.js 22+, pnpm 10, and WebView2 (see desktop/BUILDING.md).
By default, prepares uv 0.11+ and installs locked dependencies into .venv and desktop/node_modules.
-SkipDependencies reuses installed dependencies and LEGENDARY_DESKTOP_PYTHON if set.
'@
    return
}

if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw 'Run this script on Windows; cross-compiling the Python sidecar is unsupported.'
}

function Invoke-BuildCommand {
    param([string]$Command, [string[]]$Arguments)
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Command failed with exit code $LASTEXITCODE."
    }
}

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$previousPython = $env:LEGENDARY_DESKTOP_PYTHON
Push-Location $projectRoot
try {
    foreach ($tool in @('node', 'pnpm', 'cargo', 'rustc')) {
        if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
            throw "Missing build tool: $tool. See desktop/BUILDING.md."
        }
    }
    $nodeVersion = & node --version
    if ($LASTEXITCODE -ne 0) { throw 'Could not read the Node.js version.' }
    if ([int](($nodeVersion -replace '^v', '').Split('.')[0]) -lt 22) {
        throw 'Node.js 22+ is required.'
    }
    $rustInfo = & rustc -vV
    if ($LASTEXITCODE -ne 0) { throw 'Could not read the Rust host target.' }
    $target = ($rustInfo | Where-Object { $_ -match '^host: ' }) -replace '^host: ', ''
    if (-not $target -or $target -notmatch '-pc-windows-msvc$') {
        throw 'Use a native MSVC Rust toolchain, for example x86_64-pc-windows-msvc.'
    }
    if (-not $SkipDependencies) {
        & (Join-Path $PSScriptRoot 'install-uv-windows.ps1')
        Invoke-BuildCommand 'uv' @('sync', '--locked', '--python', '3.12', '--extra', 'pyinstaller_build')
        $python = Join-Path $projectRoot '.venv/Scripts/python.exe'
        Push-Location (Join-Path $projectRoot 'desktop')
        try {
            Invoke-BuildCommand 'pnpm' @('install', '--frozen-lockfile')
        } finally {
            Pop-Location
        }
    } elseif ($env:LEGENDARY_DESKTOP_PYTHON) {
        $python = $env:LEGENDARY_DESKTOP_PYTHON
    } else {
        $python = Join-Path $projectRoot '.venv/Scripts/python.exe'
    }
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw "Python environment not found: $python. Run without -SkipDependencies first."
    }
    Invoke-BuildCommand $python @('desktop/scripts/check-build-env.py', $target)
    $env:LEGENDARY_DESKTOP_PYTHON = $python
    Set-Location (Join-Path $projectRoot 'desktop')
    Invoke-BuildCommand 'pnpm' @('sidecar:build', '--target', $target)
    Invoke-BuildCommand 'pnpm' @('tauri', 'build', '--ci', '--target', $target, '--bundles', 'nsis,msi', '--', '--locked')
    $targetDirectory = if ($env:CARGO_TARGET_DIR) { $env:CARGO_TARGET_DIR } else { Join-Path $projectRoot 'desktop/src-tauri/target' }
    Write-Output "`nBuild complete ($target). Bundles: $targetDirectory/$target/release/bundle/"
} finally {
    $env:LEGENDARY_DESKTOP_PYTHON = $previousPython
    Pop-Location
}
