# Install a local post-commit hook: after every successful commit, push main to origin.
# Safe if origin is missing (skips). Never force-pushes.
#
# Usage (from repo root or any cwd):
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Install-AutoPushHook.ps1

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$HooksDir = Join-Path $RepoRoot '.git\hooks'
$HookPath = Join-Path $HooksDir 'post-commit'

if (-not (Test-Path -LiteralPath (Join-Path $RepoRoot '.git'))) {
    throw "Not a git repo: $RepoRoot"
}
if (-not (Test-Path -LiteralPath $HooksDir)) {
    throw "Missing hooks dir: $HooksDir"
}

# Git for Windows runs hooks via sh.exe — use a portable shell script.
$hookBody = @'
#!/bin/sh
# Auto-push after commit (smartLarch-Platform public staging).
# Installed by scripts/Install-AutoPushHook.ps1 — local only, not in git tree.

branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null) || exit 0
if [ "$branch" = "HEAD" ]; then
  echo "[auto-push] detached HEAD — skip"
  exit 0
fi

if ! git remote get-url origin >/dev/null 2>&1; then
  echo "[auto-push] no origin remote — skip (add origin, then push once)"
  exit 0
fi

echo "[auto-push] pushing $branch -> origin ..."
# Do not fail the commit if push fails (auth/network).
if git push -u origin "$branch"; then
  echo "[auto-push] ok"
else
  echo "[auto-push] push failed — commit is local; fix auth/remote and run: git push -u origin $branch" >&2
fi
exit 0
'@

# Write as UTF-8 no BOM, LF endings (required by sh)
$utf8 = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($HookPath, ($hookBody -replace "`r`n", "`n"), $utf8)

Write-Host "Installed: $HookPath"
Write-Host "After each commit on a named branch, git will try: git push -u origin <branch>"
Write-Host "If origin is not set yet, the hook skips until you: git remote add origin <url>"
