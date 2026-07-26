
$ErrorActionPreference = 'Continue'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

$CondaEnvName     = 'manga-env'
$LegacyEnvPath    = Join-Path $ScriptDir 'conda_env'
$DriveRoot        = [System.IO.Path]::GetPathRoot($ScriptDir)
$LocalCondaRoots  = @((Join-Path $ScriptDir 'Miniconda3'), (Join-Path $DriveRoot 'Miniconda3')) | Select-Object -Unique
$PortableGit      = Join-Path $ScriptDir 'PortableGit'

Write-Host '========================================'
Write-Host 'Legacy (Conda) Layout Uninstaller'
Write-Host '========================================'
Write-Host ''
Write-Host 'This tool removes the OLD conda-based installation:'
Write-Host "  - conda environment '$CondaEnvName'"
Write-Host "  - legacy path environment: $LegacyEnvPath"
Write-Host "  - local Miniconda3 (bundled with old installer, NOT system conda)"
Write-Host "  - PortableGit (bundled with old installer)"
Write-Host ''
Write-Host 'The new portable layout (packaging\python, packaging\uv.exe) is NOT touched.'
Write-Host ''

function Confirm-Remove([string]$Prompt) {
    $answer = Read-Host "$Prompt (y/n, default n)"
    return $answer -match '^[Yy]'
}

$removedAny = $false

# 1) Remove the named conda env via conda (system or local)
$condaExe = $null
foreach ($root in $LocalCondaRoots) {
    $exe = Join-Path $root 'Scripts\conda.exe'
    if (Test-Path $exe) { $condaExe = $exe; break }
}
if (-not $condaExe) {
    $cmd = Get-Command conda -ErrorAction SilentlyContinue
    if ($cmd) { $condaExe = 'conda' }
}
if ($condaExe) {
    $envList = & $condaExe env list 2>$null | Out-String
    if ($envList -match "(?m)^$CondaEnvName\s") {
        Write-Host "Found conda environment: $CondaEnvName"
        if (Confirm-Remove "Remove conda environment '$CondaEnvName'?") {
            & $condaExe env remove -n $CondaEnvName -y
            Write-Host "[OK] Environment '$CondaEnvName' removed"
            $removedAny = $true
        }
    } else {
        Write-Host "[INFO] Conda environment '$CondaEnvName' not found"
    }
} else {
    Write-Host '[INFO] Conda not found, skipping named environment removal'
}

# 2) Remove legacy path-based environment
if (Test-Path $LegacyEnvPath) {
    Write-Host "Found legacy path environment: $LegacyEnvPath"
    if (Confirm-Remove 'Remove it?') {
        Remove-Item -Recurse -Force $LegacyEnvPath
        Write-Host '[OK] Legacy environment removed'
        $removedAny = $true
    }
} else {
    Write-Host '[INFO] Legacy path environment not found'
}

# 3) Remove local Miniconda3 (only the ones our old installer created)
foreach ($root in $LocalCondaRoots) {
    if (Test-Path (Join-Path $root 'Scripts\conda.exe')) {
        Write-Host "Found local Miniconda3: $root"
        Write-Host '  (Skip this if you use it for anything else!)'
        if (Confirm-Remove "Remove $root ?") {
            Remove-Item -Recurse -Force $root
            Write-Host '[OK] Local Miniconda3 removed'
            $removedAny = $true
        }
    }
}

# 4) Remove PortableGit
if (Test-Path (Join-Path $PortableGit 'cmd\git.exe')) {
    Write-Host "Found PortableGit: $PortableGit"
    Write-Host '  (The new layout can still use it for updates; keep it unless unwanted.)'
    if (Confirm-Remove 'Remove PortableGit?') {
        Remove-Item -Recurse -Force $PortableGit
        Write-Host '[OK] PortableGit removed'
        $removedAny = $true
    }
} else {
    Write-Host '[INFO] PortableGit not found'
}

# 5) Optional: clean pip cache in user profile
$pipCache = Join-Path $env:LOCALAPPDATA 'pip\Cache'
if (Test-Path $pipCache) {
    if (Confirm-Remove "Clean pip cache ($pipCache)?") {
        Remove-Item -Recurse -Force $pipCache
        Write-Host '[OK] pip cache cleaned'
        $removedAny = $true
    }
}

Write-Host ''
if ($removedAny) {
    Write-Host '[DONE] Legacy cleanup finished.'
} else {
    Write-Host '[DONE] Nothing was removed.'
}
Read-Host 'Press Enter to exit'
