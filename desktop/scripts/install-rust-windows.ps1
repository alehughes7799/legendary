param(
    [ValidateSet('auto', 'x64', 'arm64')]
    [string]$Architecture = 'auto',
    [switch]$SkipBuildTools,
    [switch]$Help
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if ($Help) {
    Write-Output @'
Usage: powershell -File desktop/scripts/install-rust-windows.ps1 [-Architecture x64|arm64] [-SkipBuildTools]

Installs Rust stable and Cargo with the native Windows MSVC toolchain.
Installs Visual Studio 2022 C++ Build Tools and a Windows SDK when needed.
The Build Tools installer may request administrator access through UAC.
-SkipBuildTools installs only Rust/Cargo and skips the linker smoke check.
Existing CARGO_HOME and RUSTUP_HOME settings are respected.
'@
    return
}

if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw 'Run this script in Windows PowerShell, outside WSL.'
}

function Invoke-CheckedCommand {
    param([string]$Command, [string[]]$Arguments)
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Command failed with exit code $LASTEXITCODE."
    }
}

function Find-CppBuildTools {
    param([string]$Component)
    $programFiles = [Environment]::GetFolderPath('ProgramFilesX86')
    $vswhere = Join-Path $programFiles 'Microsoft Visual Studio/Installer/vswhere.exe'
    if (Test-Path -LiteralPath $vswhere -PathType Leaf) {
        $installation = & $vswhere -latest -products '*' -requires $Component -property installationPath
        if ($LASTEXITCODE -ne 0) { throw 'Could not query Visual Studio installations.' }
        if ($installation) { return ($installation | Select-Object -First 1) }
    }
    return $null
}

if ($Architecture -eq 'auto') {
    $nativeArchitecture = if ($env:PROCESSOR_ARCHITEW6432) {
        $env:PROCESSOR_ARCHITEW6432
    } else {
        $env:PROCESSOR_ARCHITECTURE
    }
    switch ($nativeArchitecture) {
        'AMD64' { $Architecture = 'x64' }
        'ARM64' { $Architecture = 'arm64' }
        default { throw 'This script supports 64-bit Windows (x64 or ARM64).' }
    }
}
$target = if ($Architecture -eq 'arm64') { 'aarch64-pc-windows-msvc' } else { 'x86_64-pc-windows-msvc' }
$toolchain = "stable-$target"
$component = if ($Architecture -eq 'arm64') {
    'Microsoft.VisualStudio.Component.VC.Tools.ARM64'
} else {
    'Microsoft.VisualStudio.Component.VC.Tools.x86.x64'
}
$cargoDirectory = if ($env:CARGO_HOME) { $env:CARGO_HOME } else { Join-Path $env:USERPROFILE '.cargo' }
$cargoBin = Join-Path $cargoDirectory 'bin'
$work = Join-Path ([IO.Path]::GetTempPath()) ('legendary-rust-setup-' + [Guid]::NewGuid().ToString('N'))
$previousTls = [Net.ServicePointManager]::SecurityProtocol
New-Item -ItemType Directory -Path $work | Out-Null
try {
    [Net.ServicePointManager]::SecurityProtocol = $previousTls -bor [Net.SecurityProtocolType]::Tls12
    if (-not $SkipBuildTools) {
        $installation = Find-CppBuildTools $component
        if (-not $installation) {
            Write-Output 'Installing Visual Studio 2022 C++ Build Tools and recommended Windows SDK components...'
            $installer = Join-Path $work 'vs_BuildTools.exe'
            Invoke-WebRequest -UseBasicParsing -Uri 'https://aka.ms/vs/17/release/vs_BuildTools.exe' -OutFile $installer
            $signature = Get-AuthenticodeSignature -LiteralPath $installer
            if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation(?:,|$)') {
                throw 'The Visual Studio installer does not have a valid Microsoft signature.'
            }
            $installerArguments = @('--passive', '--wait', '--norestart', '--add',
                'Microsoft.VisualStudio.Workload.VCTools', '--includeRecommended')
            if ($Architecture -eq 'arm64') { $installerArguments += @('--add', $component) }
            $result = Start-Process -FilePath $installer -ArgumentList $installerArguments -Verb RunAs -Wait -PassThru
            if ($result.ExitCode -eq 3010) {
                throw 'C++ Build Tools were installed. Restart Windows, then run this script again.'
            }
            if ($result.ExitCode -ne 0) {
                throw "Visual Studio Build Tools installation failed with exit code $($result.ExitCode)."
            }
            $installation = Find-CppBuildTools $component
            if (-not $installation) { throw 'MSVC C++ tools were not found after installation.' }
        }
        Write-Output "C++ Build Tools: $installation"
    }

    $rustup = Join-Path $cargoBin 'rustup.exe'
    if (-not (Test-Path -LiteralPath $rustup -PathType Leaf)) {
        $existingRustup = Get-Command rustup -CommandType Application -ErrorAction SilentlyContinue
        if ($existingRustup) { $rustup = $existingRustup.Source }
    }
    if (-not (Test-Path -LiteralPath $rustup -PathType Leaf)) {
        Write-Output "Downloading official Rust installer ($target)..."
        $installer = Join-Path $work 'rustup-init.exe'
        $url = "https://static.rust-lang.org/rustup/dist/$target/rustup-init.exe"
        Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $installer
        # Binary HTTP responses can expose Content as byte[] in Windows PowerShell.
        $checksumPath = Join-Path $work 'rustup-init.exe.sha256'
        Invoke-WebRequest -UseBasicParsing -Uri "$url.sha256" -OutFile $checksumPath
        $checksumText = Get-Content -LiteralPath $checksumPath -Raw -Encoding Ascii
        $expected = ($checksumText.Trim() -split '\s+')[0]
        $actual = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash
        if ($expected -notmatch '^[0-9a-fA-F]{64}$' -or $actual -ine $expected) {
            throw 'The Rust installer checksum did not match. Retry the download.'
        }
        Invoke-CheckedCommand $installer @('-y', '--profile', 'minimal', '--default-host', $target,
            '--default-toolchain', 'stable', '--no-modify-path')
        $rustup = Join-Path $cargoBin 'rustup.exe'
    }
    Invoke-CheckedCommand $rustup @('toolchain', 'install', $toolchain, '--profile', 'minimal')
    Invoke-CheckedCommand $rustup @('default', $toolchain)

    # Preserve the existing user PATH; avoid setx truncation and duplicate entries.
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    $entries = @($userPath -split ';' | Where-Object { $_ })
    if (-not ($entries | Where-Object { $_.TrimEnd('\') -ieq $cargoBin.TrimEnd('\') })) {
        $updatedPath = if ($userPath) { "$cargoBin;$userPath" } else { $cargoBin }
        [Environment]::SetEnvironmentVariable('Path', $updatedPath, 'User')
    }
    $env:Path = "$cargoBin;$env:Path"
    Invoke-CheckedCommand $rustup @('run', $toolchain, 'cargo', '--version')
    Invoke-CheckedCommand $rustup @('run', $toolchain, 'rustc', '--version')

    if (-not $SkipBuildTools) {
        $source = Join-Path $work 'rust_environment_check.rs'
        Set-Content -LiteralPath $source -Encoding Ascii -Value 'fn main() { println!("Rust and MSVC are ready."); }'
        Invoke-CheckedCommand $rustup @('run', $toolchain, 'rustc', '--edition=2021', $source, '--out-dir', $work)
        Invoke-CheckedCommand (Join-Path $work 'rust_environment_check.exe') @()
    }
    Write-Output "`nRust/Cargo setup complete: $toolchain"
    Write-Output "Cargo directory: $cargoBin"
    Write-Output 'Open a new PowerShell window before using Cargo in other terminals.'
} finally {
    [Net.ServicePointManager]::SecurityProtocol = $previousTls
    Remove-Item -LiteralPath $work -Recurse -Force
}
