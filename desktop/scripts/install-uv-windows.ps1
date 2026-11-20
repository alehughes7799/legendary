param([switch]$Help)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if ($Help) {
    Write-Output @'
Usage: powershell -File desktop/scripts/install-uv-windows.ps1

Installs uv 0.11+ using Astral's official installer, without administrator access.
Adds the installation directory to the current process and user PATH.
Reuses an existing compatible uv installation; respects UV_INSTALL_DIR.
'@
    return
}

if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw 'Run this script in Windows PowerShell, outside WSL.'
}

function Test-UvVersion {
    param([string]$Executable)
    $output = & $Executable --version
    if ($LASTEXITCODE -ne 0) { throw "Could not run uv: $Executable" }
    $versionMatch = [regex]::Match(($output -join ' '), '^uv (\d+\.\d+\.\d+)')
    if (-not $versionMatch.Success) { throw 'Could not read the installed uv version.' }
    return ([version]$versionMatch.Groups[1].Value -ge [version]'0.11.0')
}

$installDirectory = if ($env:UV_INSTALL_DIR) {
    [IO.Path]::GetFullPath($env:UV_INSTALL_DIR)
} else {
    Join-Path $env:USERPROFILE '.local/bin'
}
$uv = Join-Path $installDirectory 'uv.exe'
$existingUv = Get-Command uv -CommandType Application -ErrorAction SilentlyContinue
if ($existingUv -and (Test-UvVersion $existingUv.Source)) {
    Write-Output "Using uv: $($existingUv.Source)"
    return
}

if (-not (Test-Path -LiteralPath $uv -PathType Leaf) -or -not (Test-UvVersion $uv)) {
    Write-Output 'Installing uv from https://astral.sh/uv/install.ps1 ...'
    $work = Join-Path ([IO.Path]::GetTempPath()) ('legendary-uv-setup-' + [Guid]::NewGuid().ToString('N'))
    $previousTls = [Net.ServicePointManager]::SecurityProtocol
    $previousInstallDirectory = $env:UV_INSTALL_DIR
    $previousNoModifyPath = $env:UV_NO_MODIFY_PATH
    New-Item -ItemType Directory -Path $work | Out-Null
    try {
        [Net.ServicePointManager]::SecurityProtocol = $previousTls -bor [Net.SecurityProtocolType]::Tls12
        $installer = Join-Path $work 'uv-installer.ps1'
        Invoke-WebRequest -UseBasicParsing -Uri 'https://astral.sh/uv/install.ps1' -OutFile $installer
        $env:UV_INSTALL_DIR = $installDirectory
        $env:UV_NO_MODIFY_PATH = '1'
        $powershell = Join-Path $env:SystemRoot 'System32/WindowsPowerShell/v1.0/powershell.exe'
        # Run separately so an installer exit cannot terminate the calling build.
        & $powershell -NoProfile -ExecutionPolicy Bypass -File $installer
        if ($LASTEXITCODE -ne 0) { throw "The uv installer failed with exit code $LASTEXITCODE." }
        if (-not (Test-Path -LiteralPath $uv -PathType Leaf) -or -not (Test-UvVersion $uv)) {
            throw 'uv 0.11+ was not found after installation.'
        }
    } finally {
        $env:UV_INSTALL_DIR = $previousInstallDirectory
        $env:UV_NO_MODIFY_PATH = $previousNoModifyPath
        [Net.ServicePointManager]::SecurityProtocol = $previousTls
        Remove-Item -LiteralPath $work -Recurse -Force
    }
}

$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
$entries = @($userPath -split ';' | Where-Object { $_ })
if (-not ($entries | Where-Object { $_.TrimEnd('\') -ieq $installDirectory.TrimEnd('\') })) {
    $updatedPath = if ($userPath) { "$installDirectory;$userPath" } else { $installDirectory }
    [Environment]::SetEnvironmentVariable('Path', $updatedPath, 'User')
}
$env:Path = "$installDirectory;$env:Path"
& $uv --version
if ($LASTEXITCODE -ne 0) { throw 'uv could not start after installation.' }
Write-Output 'uv is ready. Open a new PowerShell window to use it in other terminals.'
