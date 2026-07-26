
$ErrorActionPreference = 'Continue'
$env:PYTHONUTF8 = '1'

# Use the script's own directory as the working directory
# (fixes $PWD being system32 when run as administrator)
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

# New portable layout:
#   packaging\python\python.exe  - bundled Python (with deps in site-packages)
#   packaging\uv.exe             - uv package manager (used by launch.py)
# Legacy layout (kept for compatibility):
#   Miniconda3 / system Conda with env 'manga-env' or path env 'conda_env'
$BundledPython = Join-Path $ScriptDir 'packaging\python\python.exe'
$MaintenanceScript = Join-Path $ScriptDir 'Install-or-Update.ps1'

$CondaEnvName     = 'manga-env'
$LegacyEnvPath    = Join-Path $ScriptDir 'conda_env'
$DriveRoot        = [System.IO.Path]::GetPathRoot($ScriptDir)
$AltCondaRoot     = Join-Path $DriveRoot 'Miniconda3'
$DefaultCondaRoot = Join-Path $ScriptDir 'Miniconda3'

# If the path contains non-ASCII characters (e.g. Chinese), the local
# Miniconda is expected at the drive root instead
if ($ScriptDir -match '[^\x00-\x7F]') {
    $DefaultCondaRoot = $AltCondaRoot
}

function Find-CondaRoot {
    foreach ($root in @($DefaultCondaRoot, $AltCondaRoot)) {
        if (Test-Path (Join-Path $root 'Scripts\conda.exe')) {
            Write-Host "[INFO] Found local Miniconda: $root"
            return $root
        }
    }
    if ($env:CONDA_EXE -and (Test-Path $env:CONDA_EXE)) {
        $base = & $env:CONDA_EXE info --base 2>$null
        if ($base -and (Test-Path (Join-Path $base 'Scripts\conda.exe'))) {
            Write-Host "[INFO] Found system Conda: $base"
            return $base
        }
    }
    $condaCmd = Get-Command conda -ErrorAction SilentlyContinue
    if ($condaCmd) {
        $base = & conda info --base 2>$null
        if ($base -and (Test-Path (Join-Path $base 'Scripts\conda.exe'))) {
            Write-Host "[INFO] Found system Conda: $base"
            return $base
        }
    }
    return $null
}

function Find-CondaEnvPath([string]$CondaRoot) {
    # 1) Named env under the detected conda root
    $namedEnv = Join-Path $CondaRoot "envs\$CondaEnvName"
    if (Test-Path (Join-Path $namedEnv 'python.exe')) {
        Write-Host "[INFO] Found named environment: $CondaEnvName"
        return $namedEnv
    }
    # 2) Ask conda for the env list
    $condaExe = Join-Path $CondaRoot 'Scripts\conda.exe'
    if (Test-Path $condaExe) {
        $envsLine = & $condaExe info --envs 2>$null |
            Where-Object { $_ -match "^$([regex]::Escape($CondaEnvName))\s" } |
            Select-Object -First 1
        if ($envsLine) {
            $path = ($envsLine -replace '^\S+\s+\*?\s*', '').Trim()
            if ($path -and (Test-Path (Join-Path $path 'python.exe'))) {
                Write-Host "[INFO] Found named environment: $CondaEnvName"
                return $path
            }
        }
    }
    # 3) Legacy path-based env (old installs)
    if (Test-Path (Join-Path $LegacyEnvPath 'python.exe')) {
        Write-Host '[INFO] Found legacy path-based environment'
        return $LegacyEnvPath
    }
    return $null
}

function Initialize-Environment {
    # Returns a usable python.exe path, or $null on failure.
    # Prefers the bundled portable Python; falls back to Conda for legacy installs.
    if (Test-Path $BundledPython) {
        Write-Host "[OK] Using bundled Python: $BundledPython"
        return $BundledPython
    }

    Write-Host '[INFO] Bundled Python not found, trying Conda (legacy layout)...'
    $condaRoot = Find-CondaRoot
    if (-not $condaRoot) {
        Write-Host '[ERROR] Neither bundled Python nor Conda was found.'
        Write-Host 'The package layout may be broken. Please re-download the package,'
        Write-Host 'or run the install/update tool.'
        return $null
    }
    $envPath = Find-CondaEnvPath $condaRoot
    if (-not $envPath) {
        Write-Host "[ERROR] Conda environment '$CondaEnvName' not found."
        Write-Host 'Please run the install/update tool first to create the environment.'
        return $null
    }
    # Prepend the env's runtime directories to PATH (equivalent to activation)
    $env:PATH = (@(
        $envPath,
        (Join-Path $envPath 'Library\mingw-w64\bin'),
        (Join-Path $envPath 'Library\usr\bin'),
        (Join-Path $envPath 'Library\bin'),
        (Join-Path $envPath 'Scripts'),
        (Join-Path $envPath 'bin')
    ) -join ';') + ';' + $env:PATH
    $env:CONDA_PREFIX = $envPath
    $env:CONDA_DEFAULT_ENV = $CondaEnvName
    Write-Host "[OK] Using Conda environment: $envPath"
    return (Join-Path $envPath 'python.exe')
}

function Add-PortableGitToPath {
    $portableGit = Join-Path $ScriptDir 'PortableGit\cmd'
    if (Test-Path (Join-Path $portableGit 'git.exe')) {
        $env:PATH = "$portableGit;$env:PATH"
    }
}

function Suggest-MaintenanceScript {
    Write-Host ''
    Write-Host "[HINT] Startup failed. Running the install/update tool may fix this."
    $answer = Read-Host "Open Install-or-Update.ps1 now? (y/n)"
    if ($answer -match '^[Yy]') {
        if (Test-Path $MaintenanceScript) {
            Start-Process powershell -ArgumentList '-NoExit', '-ExecutionPolicy', 'Bypass', '-File', "`"$MaintenanceScript`""
        } else {
            Write-Host "[ERROR] Script not found: $MaintenanceScript"
            Read-Host 'Press Enter to exit'
        }
    } else {
        Read-Host 'Press Enter to exit'
    }
}

Add-PortableGitToPath

$python = Initialize-Environment
if (-not $python) {
    Suggest-MaintenanceScript
    exit 1
}

Write-Host 'Starting...'
Write-Host '========================================'
Write-Host ''
& $python desktop_qt_ui\main.py
$exitCode = $LASTEXITCODE

if ($exitCode -ne 0) {
    Write-Host ''
    Write-Host "[ERROR] Application exited with code $exitCode"
    Write-Host ''
    Write-Host 'Please try reinstalling first: run Install-or-Update.ps1 and choose [1] Install.'
    Write-Host ''
    Write-Host 'If it still fails, please take a screenshot of this window and report it via:'
    Write-Host '  GitHub Issues: https://github.com/hgmzhn/manga-translator-ui/issues'
    Write-Host '  or the QQ group'
    Suggest-MaintenanceScript
    exit $exitCode
}

Write-Host ''
Read-Host 'Application closed. Press Enter to exit'
exit 0
