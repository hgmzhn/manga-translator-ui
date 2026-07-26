
$ErrorActionPreference = 'Continue'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

$CondaEnvName     = 'manga-env'
$LegacyEnvPath    = Join-Path $ScriptDir 'conda_env'
$DriveRoot        = [System.IO.Path]::GetPathRoot($ScriptDir)
$LocalCondaRoots  = @((Join-Path $ScriptDir 'Miniconda3'), (Join-Path $DriveRoot 'Miniconda3')) | Select-Object -Unique
$PortableGit      = Join-Path $ScriptDir 'PortableGit'

Write-Host '========================================'
Write-Host '旧版 (Conda) 安装卸载工具'
Write-Host '========================================'
Write-Host ''
Write-Host '本工具用于清理旧版基于 Conda 的安装内容:'
Write-Host "  - conda 环境 '$CondaEnvName'"
Write-Host "  - 旧版路径环境: $LegacyEnvPath"
Write-Host "  - 本地 Miniconda3 (旧版安装脚本自带的, 不动系统 conda)"
Write-Host "  - PortableGit (旧版安装脚本自带的)"
Write-Host ''
Write-Host '新版便携环境 (packaging\python, packaging\uv.exe) 不会被删除。'
Write-Host ''

function Confirm-Remove([string]$Prompt) {
    $answer = Read-Host "$Prompt (y/n, 默认n)"
    return $answer -match '^[Yy]'
}

$removedAny = $false

# 1) 删除 conda 命名环境（本地或系统 conda）
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
        Write-Host "检测到 conda 环境: $CondaEnvName"
        if (Confirm-Remove "是否删除 conda 环境 '$CondaEnvName'?") {
            & $condaExe env remove -n $CondaEnvName -y
            Write-Host "[OK] 环境 '$CondaEnvName' 已删除"
            $removedAny = $true
        }
    } else {
        Write-Host "[信息] 未检测到 conda 环境 '$CondaEnvName'"
    }
} else {
    Write-Host '[信息] 未检测到 conda, 跳过命名环境清理'
}

# 2) 删除旧版路径环境
if (Test-Path $LegacyEnvPath) {
    Write-Host "检测到旧版路径环境: $LegacyEnvPath"
    if (Confirm-Remove '是否删除?') {
        Remove-Item -Recurse -Force $LegacyEnvPath
        Write-Host '[OK] 旧版路径环境已删除'
        $removedAny = $true
    }
} else {
    Write-Host '[信息] 未检测到旧版路径环境'
}

# 3) 删除本地 Miniconda3（仅旧版安装脚本创建的那些位置）
foreach ($root in $LocalCondaRoots) {
    if (Test-Path (Join-Path $root 'Scripts\conda.exe')) {
        Write-Host "检测到本地 Miniconda3: $root"
        Write-Host '  (如果你还有其他用途在使用它, 请选择 n 跳过!)'
        if (Confirm-Remove "是否删除 $root ?") {
            Remove-Item -Recurse -Force $root
            Write-Host '[OK] 本地 Miniconda3 已删除'
            $removedAny = $true
        }
    }
}

# 4) 删除 PortableGit
if (Test-Path (Join-Path $PortableGit 'cmd\git.exe')) {
    Write-Host "检测到 PortableGit: $PortableGit"
    Write-Host '  (新版更新功能仍可使用它, 如无特殊需要建议保留)'
    if (Confirm-Remove '是否删除 PortableGit?') {
        Remove-Item -Recurse -Force $PortableGit
        Write-Host '[OK] PortableGit 已删除'
        $removedAny = $true
    }
} else {
    Write-Host '[信息] 未检测到 PortableGit'
}

# 5) 可选: 清理用户目录下的 pip 缓存
$pipCache = Join-Path $env:LOCALAPPDATA 'pip\Cache'
if (Test-Path $pipCache) {
    if (Confirm-Remove "是否清理 pip 缓存 ($pipCache)?") {
        Remove-Item -Recurse -Force $pipCache
        Write-Host '[OK] pip 缓存已清理'
        $removedAny = $true
    }
}

Write-Host ''
if ($removedAny) {
    Write-Host '[完成] 旧版内容清理结束。'
} else {
    Write-Host '[完成] 没有删除任何内容。'
}
Read-Host '按回车键退出'
