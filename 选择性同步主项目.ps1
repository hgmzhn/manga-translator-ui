# ========================================
# Selective Upstream Sync Tool
# Cherry-pick specific commits from upstream
# ========================================

param(
    [string]$Count = "20"
)

$upstreamRemote = "origin"
$privateRemote = "myfork"

Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "Selective Upstream Sync Tool" -ForegroundColor Green
Write-Host "Cherry-pick commits from main project" -ForegroundColor Green
Write-Host "========================================`n" -ForegroundColor Cyan

# Check Git
$gitVersion = git --version 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] Git not installed" -ForegroundColor Red
    exit 1
}
Write-Host "[SUCCESS] Git installed: $gitVersion" -ForegroundColor Green

# Get current branch
$currentBranch = git branch --show-current
Write-Host "[INFO] Current branch: $currentBranch" -ForegroundColor Cyan

# Fetch from upstream
Write-Host "`n[INFO] Fetching from upstream..." -ForegroundColor Cyan
git fetch $upstreamRemote
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] Failed to fetch from upstream" -ForegroundColor Red
    exit 1
}
Write-Host "[SUCCESS] Fetched from upstream" -ForegroundColor Green

# Show upstream commits
Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "Recent commits from main project:" -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Cyan
git log --oneline --graph $upstreamRemote/main --max-count=$Count

# Show which commits are not in current branch
Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "New commits not in your branch:" -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Cyan

$newCommits = git log --oneline $currentBranch..$upstreamRemote/main --max-count=$Count
if ($null -eq $newCommits -or $newCommits.Length -eq 0) {
    Write-Host "[INFO] Your branch is up to date with upstream" -ForegroundColor Green
    Write-Host "`nPress any key to exit..."
    $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
    exit 0
}

$newCommits | ForEach-Object {
    $commitHash = $_.Substring(0, 7)
    $commitMsg = $_.Substring(8)
    Write-Host "  $commitHash - $commitMsg" -ForegroundColor White
}

# Interactive selection
Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "Select commits to apply:" -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Options:" -ForegroundColor Cyan
Write-Host "  1. Apply specific commit by hash" -ForegroundColor White
Write-Host "  2. Apply latest N commits" -ForegroundColor White
Write-Host "  3. Apply all new commits" -ForegroundColor White
Write-Host "  4. Show commit details" -ForegroundColor White
Write-Host "  5. Exit" -ForegroundColor White
Write-Host ""

$choice = Read-Host "Select option (1-5)"

switch ($choice) {
    "1" {
        # Apply specific commit
        Write-Host "`n[INFO] Enter commit hash (first 7+ characters)" -ForegroundColor Cyan
        $hash = Read-Host "Commit hash"
        
        Write-Host "`n[INFO] Commit details:" -ForegroundColor Cyan
        git show --stat $hash
        
        Write-Host "`n[CONFIRM] Apply this commit?" -ForegroundColor Yellow
        $confirm = Read-Host "Continue? (y/n)"
        
        if ($confirm -eq "y" -or $confirm -eq "Y") {
            Write-Host "`n[INFO] Cherry-picking commit $hash..." -ForegroundColor Cyan
            git cherry-pick $hash
            
            if ($LASTEXITCODE -ne 0) {
                Write-Host "`n[ERROR] Cherry-pick failed - conflicts detected" -ForegroundColor Red
                Write-Host "[INFO] Resolve conflicts manually:" -ForegroundColor Yellow
                Write-Host "  1. Edit conflicted files" -ForegroundColor White
                Write-Host "  2. git add ." -ForegroundColor White
                Write-Host "  3. git cherry-pick --continue" -ForegroundColor White
                Write-Host "Or abort: git cherry-pick --abort" -ForegroundColor White
                exit 1
            }
            
            Write-Host "[SUCCESS] Commit applied successfully" -ForegroundColor Green
            Write-Host "`n[INFO] Push to private repo?" -ForegroundColor Cyan
            $push = Read-Host "Push to $privateRemote/$currentBranch? (y/n)"
            
            if ($push -eq "y" -or $push -eq "Y") {
                git push $privateRemote $currentBranch
                if ($LASTEXITCODE -eq 0) {
                    Write-Host "[SUCCESS] Pushed to private repo" -ForegroundColor Green
                } else {
                    Write-Host "[ERROR] Push failed" -ForegroundColor Red
                }
            }
        } else {
            Write-Host "[INFO] Operation cancelled" -ForegroundColor Yellow
        }
    }
    
    "2" {
        # Apply latest N commits
        Write-Host "`n[INFO] How many latest commits to apply?" -ForegroundColor Cyan
        $n = Read-Host "Number of commits"
        
        if ($n -match '^\d+$' -and [int]$n -gt 0) {
            $commits = git log --oneline $currentBranch..$upstreamRemote/main --max-count=$n | ForEach-Object { $_.Substring(0, 7) }
            $commits = $commits | Sort-Object { git rev-list --count $_ }
            
            Write-Host "`n[INFO] Will apply these commits (oldest first):" -ForegroundColor Cyan
            $commits | ForEach-Object {
                $msg = git log --oneline -1 $_
                Write-Host "  $msg" -ForegroundColor White
            }
            
            Write-Host "`n[CONFIRM] Apply these commits?" -ForegroundColor Yellow
            $confirm = Read-Host "Continue? (y/n)"
            
            if ($confirm -eq "y" -or $confirm -eq "Y") {
                $failed = $false
                foreach ($commit in $commits) {
                    Write-Host "`n[INFO] Cherry-picking $commit..." -ForegroundColor Cyan
                    git cherry-pick $commit
                    
                    if ($LASTEXITCODE -ne 0) {
                        Write-Host "[ERROR] Cherry-pick failed at $commit" -ForegroundColor Red
                        Write-Host "[INFO] Resolve conflicts and run: git cherry-pick --continue" -ForegroundColor Yellow
                        Write-Host "Or abort: git cherry-pick --abort" -ForegroundColor White
                        $failed = $true
                        break
                    }
                    Write-Host "[SUCCESS] Applied $commit" -ForegroundColor Green
                }
                
                if (-not $failed) {
                    Write-Host "`n[SUCCESS] All commits applied successfully" -ForegroundColor Green
                    Write-Host "`n[INFO] Push to private repo?" -ForegroundColor Cyan
                    $push = Read-Host "Push to $privateRemote/$currentBranch? (y/n)"
                    
                    if ($push -eq "y" -or $push -eq "Y") {
                        git push $privateRemote $currentBranch
                        if ($LASTEXITCODE -eq 0) {
                            Write-Host "[SUCCESS] Pushed to private repo" -ForegroundColor Green
                        }
                    }
                }
            } else {
                Write-Host "[INFO] Operation cancelled" -ForegroundColor Yellow
            }
        } else {
            Write-Host "[ERROR] Invalid number" -ForegroundColor Red
        }
    }
    
    "3" {
        # Apply all new commits
        Write-Host "`n[WARNING] This will apply ALL new commits from upstream" -ForegroundColor Yellow
        Write-Host "[INFO] Total commits to apply: $($newCommits.Count)" -ForegroundColor Cyan
        
        Write-Host "`n[CONFIRM] Apply all commits?" -ForegroundColor Yellow
        $confirm = Read-Host "Continue? (y/n)"
        
        if ($confirm -eq "y" -or $confirm -eq "Y") {
            Write-Host "`n[INFO] Merging upstream/main..." -ForegroundColor Cyan
            git merge $upstreamRemote/main --no-edit
            
            if ($LASTEXITCODE -ne 0) {
                Write-Host "`n[ERROR] Merge failed - conflicts detected" -ForegroundColor Red
                Write-Host "[INFO] Resolve conflicts manually:" -ForegroundColor Yellow
                Write-Host "  1. Edit conflicted files" -ForegroundColor White
                Write-Host "  2. git add ." -ForegroundColor White
                Write-Host "  3. git commit" -ForegroundColor White
                Write-Host "Or abort: git merge --abort" -ForegroundColor White
                exit 1
            }
            
            Write-Host "[SUCCESS] All commits applied" -ForegroundColor Green
            Write-Host "`n[INFO] Push to private repo?" -ForegroundColor Cyan
            $push = Read-Host "Push to $privateRemote/$currentBranch? (y/n)"
            
            if ($push -eq "y" -or $push -eq "Y") {
                git push $privateRemote $currentBranch
                if ($LASTEXITCODE -eq 0) {
                    Write-Host "[SUCCESS] Pushed to private repo" -ForegroundColor Green
                }
            }
        } else {
            Write-Host "[INFO] Operation cancelled" -ForegroundColor Yellow
        }
    }
    
    "4" {
        # Show commit details
        Write-Host "`n[INFO] Enter commit hash to view details" -ForegroundColor Cyan
        $hash = Read-Host "Commit hash"
        
        Write-Host "`n========================================" -ForegroundColor Cyan
        git show $hash
        Write-Host "========================================" -ForegroundColor Cyan
    }
    
    "5" {
        Write-Host "`n[INFO] Exiting..." -ForegroundColor Cyan
        exit 0
    }
    
    default {
        Write-Host "`n[ERROR] Invalid option" -ForegroundColor Red
        exit 1
    }
}

Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "Operation Complete!" -ForegroundColor Green
Write-Host "========================================`n" -ForegroundColor Cyan
