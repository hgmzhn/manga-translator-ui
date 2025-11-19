# 私有仓库同步工具
# UTF-8 with BOM encoding

param(
    [Parameter(Mandatory=$false)]
    [ValidateSet("push", "pull")]
    [string]$Action,
    
    [Parameter(Mandatory=$false)]
    [ValidateSet("don", "my-custom-features", "qwe")]
    [string]$Branch
)

# 确保在脚本所在目录运行
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

$remoteName = "my-private"
$remoteUrl = "https://gitee.com/long-xin12/manga-translator-ui.git"

# 分支说明
$branchInfo = @{
    "don" = "流水线功能（新四线流水线：删除分段+修复并发）"
    "my-custom-features" = "UI功能（漫画管理面板+CBZ工具等）"
    "qwe" = "长图拼接功能（智能拼接+翻译count mismatch修复）"
}

Write-Host "[信息] 脚本运行目录: $scriptDir" -ForegroundColor Cyan

# 检查是否在Git仓库中
if (-not (Test-Path ".git")) {
    Write-Host "[错误] 当前目录不是Git仓库" -ForegroundColor Red
    Write-Host "[信息] 当前目录: $(Get-Location)" -ForegroundColor Cyan
    Write-Host "[建议] 请将脚本放在Git仓库根目录下运行" -ForegroundColor Yellow
    exit 1
}

# 检查Git
$gitVersion = git --version 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[错误] 未找到Git" -ForegroundColor Red
    exit 1
}
Write-Host "[成功] Git已安装: $gitVersion" -ForegroundColor Green

# 检查远程仓库
$existingUrl = git remote get-url $remoteName 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[警告] 未找到远程仓库 '$remoteName'" -ForegroundColor Yellow
    Write-Host "[信息] 当前远程仓库列表:" -ForegroundColor Cyan
    git remote -v
    
    Write-Host "
[询问] 是否要添加远程仓库 '$remoteName' ?" -ForegroundColor Yellow
    Write-Host "[信息] URL: $remoteUrl" -ForegroundColor Cyan
    $addRemote = Read-Host "输入 Y 添加，其他键跳过"
    
    if ($addRemote -eq "Y" -or $addRemote -eq "y") {
        git remote add $remoteName $remoteUrl
        if ($LASTEXITCODE -eq 0) {
            Write-Host "[成功] 已添加远程仓库 '$remoteName'" -ForegroundColor Green
        } else {
            Write-Host "[错误] 添加远程仓库失败" -ForegroundColor Red
            exit 1
        }
    } else {
        Write-Host "[信息] 跳过添加远程仓库" -ForegroundColor Cyan
        exit 0
    }
} else {
    Write-Host "[成功] 远程仓库: $existingUrl" -ForegroundColor Green
}

# 获取当前分支
$currentBranch = git branch --show-current
Write-Host "[信息] 当前分支: $currentBranch" -ForegroundColor Cyan

# 检查未提交的修改
$status = git status --porcelain
$hasChanges = $null -ne $status -and $status.Length -gt 0

if ($hasChanges) {
    Write-Host "[信息] 检测到未提交的修改" -ForegroundColor Yellow
}

# 如果未指定分支，询问用户
if (-not $Branch) {
    Write-Host "`n========================================" -ForegroundColor Cyan
    Write-Host "请选择要同步的分支:" -ForegroundColor Yellow
    Write-Host "  1. don - $($branchInfo['don'])" -ForegroundColor White
    Write-Host "  2. my-custom-features - $($branchInfo['my-custom-features'])" -ForegroundColor White
    Write-Host "  3. qwe - $($branchInfo['qwe'])" -ForegroundColor White
    Write-Host "========================================`n" -ForegroundColor Cyan
    
    $branchChoice = Read-Host "请输入选择 (1/2/3)"
    
    switch ($branchChoice) {
        "1" { $Branch = "don" }
        "2" { $Branch = "my-custom-features" }
        "3" { $Branch = "qwe" }
        default {
            Write-Host "[错误] 无效的选择" -ForegroundColor Red
            exit 1
        }
    }
}

Write-Host "[信息] 选择的分支: $Branch - $($branchInfo[$Branch])" -ForegroundColor Cyan

# 如果未指定操作，询问用户
if (-not $Action) {
    Write-Host "`n========================================" -ForegroundColor Cyan
    Write-Host "请选择操作:" -ForegroundColor Yellow
    Write-Host "  1. 推送 - 将本地修改推送到远程" -ForegroundColor White
    Write-Host "  2. 拉取 - 从远程拉取最新修改" -ForegroundColor White  
    Write-Host "========================================`n" -ForegroundColor Cyan
    
    $choice = Read-Host "请输入选择 (1/2)"
    
    switch ($choice) {
        "1" { $Action = "push" }
        "2" { $Action = "pull" }
        default {
            Write-Host "[错误] 无效的选择" -ForegroundColor Red
            exit 1
        }
    }
}

Write-Host "
========================================" -ForegroundColor Cyan
Write-Host "执行操作: $(if($Action -eq 'push'){'推送'}else{'拉取'})" -ForegroundColor Yellow
Write-Host "========================================
" -ForegroundColor Cyan

# 检查目标分支是否存在
$branchExists = git rev-parse --verify $Branch 2>$null
if ($LASTEXITCODE -ne 0) {
    # 检查远程分支是否存在
    git fetch $remoteName 2>$null
    $remoteBranchExists = git rev-parse --verify $remoteName/$Branch 2>$null
    
    if ($LASTEXITCODE -eq 0) {
        Write-Host "[信息] 本地不存在分支 '$Branch'，但远程有此分支" -ForegroundColor Yellow
        Write-Host "[询问] 是否要创建并切换到 '$Branch' 分支?" -ForegroundColor Yellow
        $createBranch = Read-Host "输入 Y 创建，其他键使用当前分支"
        
        if ($createBranch -eq "Y" -or $createBranch -eq "y") {
            if ($hasChanges) {
                Write-Host "[信息] 暂存当前修改..." -ForegroundColor Yellow
                git stash push -m "切换分支前自动暂存"
            }
            
            git checkout -b $Branch $remoteName/$Branch
            if ($LASTEXITCODE -eq 0) {
                Write-Host "[成功] 已创建并切换到 '$Branch'" -ForegroundColor Green
                $targetBranch = $Branch
            } else {
                Write-Host "[错误] 创建分支失败，使用当前分支" -ForegroundColor Red
                $targetBranch = $currentBranch
            }
        } else {
            Write-Host "[信息] 使用当前分支 '$currentBranch'" -ForegroundColor Cyan
            $targetBranch = $currentBranch
        }
    } else {
        Write-Host "[信息] 本地和远程都不存在分支 '$Branch'" -ForegroundColor Yellow
        Write-Host "[信息] 使用当前分支 '$currentBranch'" -ForegroundColor Cyan
        $targetBranch = $currentBranch
    }
} else {
    # 分支存在，切换到该分支
    if ($currentBranch -ne $Branch) {
        if ($hasChanges) {
            Write-Host "[信息] 暂存当前修改..." -ForegroundColor Yellow
            git stash push -m "切换分支前自动暂存"
        }
        
        git checkout $Branch
        if ($LASTEXITCODE -ne 0) {
            Write-Host "[错误] 切换分支失败" -ForegroundColor Red
            if ($hasChanges) { git stash pop }
            exit 1
        }
        Write-Host "[成功] 已切换到 '$Branch'" -ForegroundColor Green
        $targetBranch = $Branch
    } else {
        Write-Host "[成功] 已在分支 '$Branch'" -ForegroundColor Green
        $targetBranch = $Branch
    }
}

if ($Action -eq "pull") {
    # 拉取操作
    Write-Host "
[拉取] 获取远程更新..." -ForegroundColor Cyan
    git fetch $remoteName
    
    # 检查是否有未提交的修改
    $statusBeforePull = git status --porcelain
    if ($statusBeforePull) {
        Write-Host "[信息] 检测到未提交的修改，将自动暂存..." -ForegroundColor Yellow
        git stash push -m "拉取前自动暂存"
        if ($LASTEXITCODE -eq 0) {
            $stashed = $true
            Write-Host "[成功] 已暂存修改" -ForegroundColor Green
        } else {
            Write-Host "[警告] 暂存失败或无需暂存" -ForegroundColor Yellow
            $stashed = $false
        }
    } else {
        $stashed = $false
    }
    
    Write-Host "[拉取] 从 $remoteName/$targetBranch 拉取..." -ForegroundColor Cyan
    git pull $remoteName $targetBranch
    
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[错误] 拉取失败" -ForegroundColor Red
        if ($stashed) { 
            Write-Host "[信息] 尝试恢复暂存的修改..." -ForegroundColor Cyan
            git stash pop 
        }
        exit 1
    }
    
    Write-Host "[成功] 拉取完成" -ForegroundColor Green
    
    # 恢复暂存的修改
    if ($stashed) {
        Write-Host "[信息] 恢复暂存的修改..." -ForegroundColor Yellow
        git stash pop
        if ($LASTEXITCODE -ne 0) {
            Write-Host "[警告] 恢复时发生冲突" -ForegroundColor Yellow
            Write-Host "[信息] 请手动解决冲突: git status" -ForegroundColor Cyan
            Write-Host "[信息] 解决后运行: git add . && git commit" -ForegroundColor Cyan
        } else {
            Write-Host "[成功] 已恢复暂存的修改" -ForegroundColor Green
        }
    }
    
} else {
    # 推送操作
    $uncommitted = git status --porcelain
    if ($uncommitted) {
        Write-Host "[信息] 发现未提交的修改，将自动提交..." -ForegroundColor Yellow
        git add -A
        
        $commitMsg = Read-Host "请输入提交信息 (留空使用自动信息)"
        if ([string]::IsNullOrWhiteSpace($commitMsg)) {
            $commitMsg = "自动提交: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
        }
        
        git commit -m $commitMsg
        if ($LASTEXITCODE -ne 0) {
            Write-Host "[错误] 提交失败" -ForegroundColor Red
            exit 1
        }
        Write-Host "[成功] 修改已提交" -ForegroundColor Green
    }
    
    Write-Host "
[推送] 推送到 $remoteName/$targetBranch..." -ForegroundColor Cyan
    git push $remoteName $targetBranch
    
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[错误] 推送失败" -ForegroundColor Red
        Write-Host "[信息] 可能需要先拉取: git pull $remoteName $targetBranch" -ForegroundColor Cyan
        exit 1
    }
    
    Write-Host "[成功] 推送完成" -ForegroundColor Green
}

# 显示最近的提交
Write-Host "
[信息] 最近的提交:" -ForegroundColor Cyan
git log --oneline --graph -3

Write-Host "
========================================" -ForegroundColor Green
Write-Host "操作成功完成！" -ForegroundColor Green
Write-Host "========================================
" -ForegroundColor Green