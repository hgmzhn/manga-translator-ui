# Private Repository Update Tool
# Purpose: Pull from loqwe/manga-translator-ui and optionally sync with upstream

param(
    [Parameter(Mandatory=$false)]
    [ValidateSet("main", "dev")]
    [string]$Branch = "main",
    
    [Parameter(Mandatory=$false)]
    [switch]$SyncUpstream = $false
)

# Run in script directory
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

$privateRemote = "myfork"    # loqwe/manga-translator-ui (private repo)
$upstreamRemote = "origin"   # hgmzhn/manga-translator-ui (upstream)

Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "Private Repository Update Tool" -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Private Repo: loqwe/manga-translator-ui" -ForegroundColor White
Write-Host "Upstream: hgmzhn/manga-translator-ui" -ForegroundColor White
Write-Host "Target Branch: $Branch" -ForegroundColor White
if ($SyncUpstream) {
    Write-Host "Sync Upstream: Yes" -ForegroundColor Green
}
Write-Host "========================================`n" -ForegroundColor Cyan

# Check Git repository
if (-not (Test-Path ".git")) {
    Write-Host "[ERROR] Not a Git repository" -ForegroundColor Red
    Write-Host "[INFO] Current directory: $(Get-Location)" -ForegroundColor Cyan
    exit 1
}

# Check Git
$gitVersion = git --version 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] Git not found" -ForegroundColor Red
    exit 1
}
Write-Host "[SUCCESS] Git installed: $gitVersion" -ForegroundColor Green

# Check private remote
$privateUrl = git remote get-url $privateRemote 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] Private remote '$privateRemote' not found" -ForegroundColor Red
    Write-Host "[INFO] Current remotes:" -ForegroundColor Cyan
    git remote -v
    exit 1
}
Write-Host "[SUCCESS] Private repo: $privateUrl" -ForegroundColor Green

# Check upstream remote
$upstreamUrl = git remote get-url $upstreamRemote 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[WARNING] Upstream remote '$upstreamRemote' not found" -ForegroundColor Yellow
    $hasUpstream = $false
} else {
    Write-Host "[SUCCESS] Upstream: $upstreamUrl" -ForegroundColor Green
    $hasUpstream = $true
}

# Get current branch
$currentBranch = git branch --show-current
Write-Host "[INFO] Current branch: $currentBranch" -ForegroundColor Cyan

# Check uncommitted changes
$status = git status --porcelain
$hasChanges = $null -ne $status -and $status.Length -gt 0

if ($hasChanges) {
    Write-Host "[WARNING] Uncommitted changes detected" -ForegroundColor Yellow
    Write-Host "[INFO] Stashing changes..." -ForegroundColor Cyan
    git stash push -m "Auto-stash before private repo update $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    if ($LASTEXITCODE -eq 0) {
        $stashed = $true
        Write-Host "[SUCCESS] Changes stashed" -ForegroundColor Green
    } else {
        Write-Host "[ERROR] Stash failed" -ForegroundColor Red
        exit 1
    }
} else {
    $stashed = $false
}

# Step 1: Fetch from private repo
Write-Host "`n[STEP 1/4] Fetching from private repository..." -ForegroundColor Cyan
git fetch $privateRemote
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] Fetch from private repo failed" -ForegroundColor Red
    if ($stashed) { git stash pop }
    exit 1
}
Write-Host "[SUCCESS] Fetched from private repository" -ForegroundColor Green

# Step 2: Sync with upstream if needed
if ($SyncUpstream -and $hasUpstream) {
    Write-Host "`n[STEP 2/4] Syncing with upstream..." -ForegroundColor Cyan
    
    # Fetch from upstream
    git fetch $upstreamRemote
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] Fetch from upstream failed" -ForegroundColor Red
        if ($stashed) { git stash pop }
        exit 1
    }
    Write-Host "[SUCCESS] Fetched from upstream" -ForegroundColor Green
    
    # Check if upstream has updates
    $upstreamCommit = git rev-parse $upstreamRemote/$Branch 2>$null
    $privateCommit = git rev-parse $privateRemote/$Branch 2>$null
    
    if ($LASTEXITCODE -eq 0 -and $upstreamCommit -ne $privateCommit) {
        Write-Host "[INFO] New updates from upstream:" -ForegroundColor Cyan
        git log --oneline $privateRemote/$Branch..$upstreamRemote/$Branch --max-count=10
        $hasUpstreamUpdates = $true
    } else {
        Write-Host "[INFO] Private repo is up to date with upstream" -ForegroundColor Cyan
        $hasUpstreamUpdates = $false
    }
} else {
    Write-Host "`n[STEP 2/4] Skipping upstream sync" -ForegroundColor Yellow
    $hasUpstreamUpdates = $false
}

# Step 3: Switch and update branch
Write-Host "`n[STEP 3/4] Updating local branch..." -ForegroundColor Cyan

# Check if target branch exists
git rev-parse --verify $Branch 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    # Create local branch from private repo
    Write-Host "[INFO] Branch '$Branch' not found locally, creating from private repo" -ForegroundColor Yellow
    git checkout -b $Branch $privateRemote/$Branch
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] Failed to create branch" -ForegroundColor Red
        if ($stashed) { git stash pop }
        exit 1
    }
    Write-Host "[SUCCESS] Created and switched to '$Branch'" -ForegroundColor Green
} else {
    # Switch to target branch
    if ($currentBranch -ne $Branch) {
        git checkout $Branch
        if ($LASTEXITCODE -ne 0) {
            Write-Host "[ERROR] Failed to switch branch" -ForegroundColor Red
            if ($stashed) { git stash pop }
            exit 1
        }
        Write-Host "[SUCCESS] Switched to '$Branch'" -ForegroundColor Green
    }
}

# Update local branch
$beforeCommit = git rev-parse HEAD

if ($SyncUpstream -and $hasUpstreamUpdates) {
    # Pull from private repo first, then merge upstream
    Write-Host "[INFO] Pulling from private repo first..." -ForegroundColor Cyan
    git pull $privateRemote $Branch
    
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] Pull from private repo failed" -ForegroundColor Red
        if ($stashed) { git stash pop }
        exit 1
    }
    
    Write-Host "[INFO] Merging upstream updates..." -ForegroundColor Cyan
    git merge $upstreamRemote/$Branch --no-edit
    
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] Failed to merge upstream updates, conflicts may exist" -ForegroundColor Red
        Write-Host "[INFO] Resolve conflicts manually:" -ForegroundColor Yellow
        Write-Host "  git add ." -ForegroundColor White
        Write-Host "  git commit" -ForegroundColor White
        Write-Host "  git push myfork $Branch" -ForegroundColor White
        if ($stashed) {
            Write-Host "[INFO] After resolving: git stash pop" -ForegroundColor Yellow
        }
        exit 1
    }
    
    Write-Host "[SUCCESS] Merged upstream updates" -ForegroundColor Green
} else {
    # Pull from private repo only
    git pull $privateRemote $Branch
    
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] Pull failed" -ForegroundColor Red
        if ($stashed) { git stash pop }
        exit 1
    }
}

$afterCommit = git rev-parse HEAD
if ($beforeCommit -eq $afterCommit) {
    Write-Host "[INFO] Already up to date" -ForegroundColor Cyan
} else {
    Write-Host "[SUCCESS] Local branch updated" -ForegroundColor Green
    Write-Host "[INFO] Changes:" -ForegroundColor Cyan
    git log --oneline $beforeCommit..$afterCommit --max-count=5
}

# Step 4: Push to private repo if synced with upstream
if ($SyncUpstream -and $hasUpstreamUpdates -and $beforeCommit -ne $afterCommit) {
    Write-Host "`n[STEP 4/4] Pushing updates to private repo..." -ForegroundColor Cyan
    
    Write-Host "[CONFIRM] Ready to push upstream updates to private repo" -ForegroundColor Yellow
    $confirm = Read-Host "Continue? (Y/N)"
    
    if ($confirm -eq "Y" -or $confirm -eq "y") {
        git push $privateRemote $Branch
        
        if ($LASTEXITCODE -ne 0) {
            Write-Host "[ERROR] Push to private repo failed" -ForegroundColor Red
            Write-Host "[INFO] You can push manually later: git push myfork $Branch" -ForegroundColor Yellow
        } else {
            Write-Host "[SUCCESS] Pushed to private repo" -ForegroundColor Green
        }
    } else {
        Write-Host "[INFO] Skipped push, you can push manually later: git push myfork $Branch" -ForegroundColor Cyan
    }
} else {
    Write-Host "`n[STEP 4/4] No push needed" -ForegroundColor Yellow
}

# Restore stashed changes
if ($stashed) {
    Write-Host "`n[RESTORE] Restoring stashed changes..." -ForegroundColor Cyan
    
    # Switch back to original branch if needed
    if ($currentBranch -ne $Branch) {
        git checkout $currentBranch 2>$null
    }
    
    git stash pop
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[WARNING] Conflicts during restore" -ForegroundColor Yellow
        Write-Host "[INFO] Resolve manually: git status" -ForegroundColor Cyan
    } else {
        Write-Host "[SUCCESS] Stashed changes restored" -ForegroundColor Green
    }
}

# Show final status
Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "Update Complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Current branch: $(git branch --show-current)" -ForegroundColor White
Write-Host "Private repo: https://github.com/loqwe/manga-translator-ui" -ForegroundColor White
Write-Host "Recent commits:" -ForegroundColor Cyan
git log --oneline --graph -3
Write-Host "========================================`n" -ForegroundColor Green
