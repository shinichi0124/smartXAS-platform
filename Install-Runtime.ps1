# Install a private Miniconda + conda-forge env with xraylarch + PySide6 for smartLarch Platform.
# Target (no admin required):
#   %LOCALAPPDATA%\smartLarch\Miniconda3\envs\smartlarch\
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\Install-Runtime.ps1
#   powershell ... -File .\Install-Runtime.ps1 -Force   # reinstall env
#   powershell ... -File .\Install-Runtime.ps1 -Yes     # no confirmation prompt
#
# ASCII only. See Launch-smartLarch-Platform.ps1 (v0.35.14): PS 5.1 parses
# .ps1 as the system ANSI code page. En-dashes in this file would crash
# first-run on English Windows before any log is written.

[CmdletBinding()]
param(
    [switch]$Force,
    [switch]$Yes
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
# Log sits next to this script (same folder as the extracted zip / .bat).
$Log = Join-Path $Root "smartLarch-runtime-install.log"

function Write-Log([string]$msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    Add-Content -LiteralPath $Log -Value $line -Encoding UTF8
    Write-Host $line
}

$ManagedRoot = Join-Path $env:LOCALAPPDATA "smartLarch"
$MinicondaRoot = Join-Path $ManagedRoot "Miniconda3"
$EnvName = "smartlarch"
$EnvPrefix = Join-Path $MinicondaRoot "envs\$EnvName"
$CondaExe = Join-Path $MinicondaRoot "Scripts\conda.exe"
$PyExe = Join-Path $EnvPrefix "python.exe"
$Marker = Join-Path $ManagedRoot "runtime.json"

# Immutable official artifact (published SHA-256 from repo.anaconda.com).
$MinicondaUrl = "https://repo.anaconda.com/miniconda/Miniconda3-py312_25.7.0-2-Windows-x86_64.exe"
$MinicondaSha256 = "97a750503cf1ef57c8a7cc79e7cff04b3d4a566091c631f665eb91eae3a6fde0"
$Installer = Join-Path $env:TEMP "Miniconda3-smartLarch-setup.exe"

Write-Log "=== smartLarch runtime install ==="
Write-Log "ManagedRoot=$ManagedRoot"
Write-Log "EnvPrefix=$EnvPrefix"
Write-Log "Log=$Log"

function Test-EnvHealthy {
    param([string]$PythonPath, [switch]$RequirePymcr, [switch]$RequireOpenpyxl)
    if (-not (Test-Path -LiteralPath $PythonPath)) { return $false }
    $tag = "{0}_{1}" -f $PID, [guid]::NewGuid().ToString("N")
    $outFile = Join-Path $env:TEMP "sl_rt_out_$tag.txt"
    $errFile = Join-Path $env:TEMP "sl_rt_err_$tag.txt"
    $oldPath = $env:PATH
    try {
        $envRoot = Split-Path -Parent $PythonPath
        $prepend = @(
            (Join-Path $envRoot "Library\bin"),
            (Join-Path $envRoot "Library\usr\bin"),
            (Join-Path $envRoot "Scripts"),
            $envRoot
        ) | Where-Object { $_ -and (Test-Path $_) }
        if ($prepend) {
            $env:PATH = ($prepend + @($oldPath)) -join ";"
        }
        $mods = @("larch", "PySide6")
        if ($RequirePymcr) { $mods += "pymcr" }
        if ($RequireOpenpyxl) { $mods += "openpyxl" }
        $code = "import " + ($mods -join ", ") + "; print('OK')"
        $p = Start-Process -FilePath $PythonPath -ArgumentList @("-c", $code) -Wait -PassThru -NoNewWindow `
            -RedirectStandardOutput $outFile `
            -RedirectStandardError $errFile
        if ($p.ExitCode -ne 0) { return $false }
        $out = Get-Content $outFile -Raw -ErrorAction SilentlyContinue
        return ($out -match "OK")
    } catch {
        return $false
    } finally {
        $env:PATH = $oldPath
        Remove-Item -LiteralPath $outFile, $errFile -Force -ErrorAction SilentlyContinue
    }
}

function Install-Pymcr {
    param([string]$PythonPath)
    # pyMCR is not always on conda-forge; pip into the *same* env is reliable for new users.
    Write-Log "Ensuring pymcr (MCR-ALS) via pip in $PythonPath"
    $pipOut = Join-Path $env:TEMP "smartLarch-pip-pymcr.txt"
    $pipErr = Join-Path $env:TEMP "smartLarch-pip-pymcr-err.txt"
    $p = Start-Process -FilePath $PythonPath -ArgumentList @(
        "-m", "pip", "install", "pymcr==0.5.1"
    ) -Wait -PassThru -NoNewWindow `
        -RedirectStandardOutput $pipOut `
        -RedirectStandardError $pipErr
    Write-Log "pip pymcr exit=$($p.ExitCode)"
    Get-Content $pipOut -ErrorAction SilentlyContinue | ForEach-Object { Write-Log $_ }
    Get-Content $pipErr -ErrorAction SilentlyContinue | ForEach-Object { Write-Log "ERR $_" }
    return (Test-EnvHealthy $PythonPath -RequirePymcr)
}

function Install-Openpyxl {
    param([string]$PythonPath)
    # Excel .xlsx export (Tools -> Export) needs openpyxl; pip into the *same* env.
    Write-Log "Ensuring openpyxl (Excel export) via pip in $PythonPath"
    $pipOut = Join-Path $env:TEMP "smartLarch-pip-openpyxl.txt"
    $pipErr = Join-Path $env:TEMP "smartLarch-pip-openpyxl-err.txt"
    $p = Start-Process -FilePath $PythonPath -ArgumentList @(
        "-m", "pip", "install", "openpyxl"
    ) -Wait -PassThru -NoNewWindow `
        -RedirectStandardOutput $pipOut `
        -RedirectStandardError $pipErr
    Write-Log "pip openpyxl exit=$($p.ExitCode)"
    Get-Content $pipOut -ErrorAction SilentlyContinue | ForEach-Object { Write-Log $_ }
    Get-Content $pipErr -ErrorAction SilentlyContinue | ForEach-Object { Write-Log "ERR $_" }
    return (Test-EnvHealthy $PythonPath -RequireOpenpyxl)
}

if ((-not $Force) -and (Test-EnvHealthy $PyExe)) {
    if (-not (Test-EnvHealthy $PyExe -RequirePymcr)) {
        Write-Log "Core runtime OK but pymcr missing - installing for MCR-ALS"
        if (-not (Install-Pymcr $PyExe)) {
            Write-Log "WARN: pymcr install failed; MCR-ALS will be unavailable until fixed"
        }
    }
    if (-not (Test-EnvHealthy $PyExe -RequireOpenpyxl)) {
        Write-Log "Core runtime OK but openpyxl missing - installing for Excel export"
        if (-not (Install-Openpyxl $PyExe)) {
            Write-Log "WARN: openpyxl install failed; Excel (.xlsx) export will be unavailable until fixed"
        }
    }
    Write-Log "Runtime already healthy: $PyExe"
    $info = @{
        schema = "smartlarch-runtime/v1"
        python = $PyExe
        env_prefix = $EnvPrefix
        miniconda = $MinicondaRoot
        installed_at = (Get-Date).ToString("o")
        source = "existing"
        packages = @("xraylarch", "pyside6", "matplotlib", "numpy", "scipy", "pymcr", "openpyxl")
    } | ConvertTo-Json
    Set-Content -Path $Marker -Value $info -Encoding UTF8
    Write-Host ""
    Write-Host "Runtime OK. Launch smartLarch Platform normally."
    exit 0
}

if (-not $Yes) {
    Add-Type -AssemblyName System.Windows.Forms -ErrorAction SilentlyContinue
    $msg = @"
smartLarch Platform needs a scientific Python environment (xraylarch + PySide6).

This will download Miniconda and install packages into:
  $EnvPrefix

- Size: roughly 1-2 GB
- Time: often 10-30 minutes (network dependent)
- No administrator rights required
- Internet connection required

Continue?
"@
    $r = [System.Windows.Forms.MessageBox]::Show(
        $msg,
        "smartLarch Platform - Install runtime",
        [System.Windows.Forms.MessageBoxButtons]::YesNo,
        [System.Windows.Forms.MessageBoxIcon]::Information
    )
    if ($r -ne [System.Windows.Forms.DialogResult]::Yes) {
        Write-Log "User cancelled"
        exit 2
    }
}

# --- Miniconda base ---
if (-not (Test-Path -LiteralPath $CondaExe)) {
    if (Test-Path -LiteralPath $MinicondaRoot) {
        Write-Log "Incomplete Miniconda tree (no conda.exe). Removing $MinicondaRoot"
        Remove-Item -LiteralPath $MinicondaRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
    Write-Log "Downloading Miniconda from $MinicondaUrl"
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri $MinicondaUrl -OutFile $Installer -UseBasicParsing
    } catch {
        Write-Log "Download failed: $_"
        throw "Failed to download Miniconda. Check network / proxy. See $Log"
    }
    $actualSha256 = (Get-FileHash -LiteralPath $Installer -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualSha256 -ne $MinicondaSha256) {
        Remove-Item -LiteralPath $Installer -Force -ErrorAction SilentlyContinue
        throw "Miniconda SHA-256 mismatch. Expected $MinicondaSha256; got $actualSha256"
    }
    Write-Log "Verified Miniconda SHA-256: $actualSha256"
    Write-Log "Running silent Miniconda installer -> $MinicondaRoot"
    New-Item -ItemType Directory -Path $ManagedRoot -Force | Out-Null
    $args = "/InstallationType=JustMe /RegisterPython=0 /AddToPath=0 /S /D=$MinicondaRoot"
    $p = Start-Process -FilePath $Installer -ArgumentList $args -Wait -PassThru
    Write-Log "Miniconda installer exit=$($p.ExitCode)"
    if ($p.ExitCode -ne 0 -and -not (Test-Path $CondaExe)) {
        throw "Miniconda install failed (exit $($p.ExitCode)). See $Log"
    }
} else {
    Write-Log "Miniconda already present: $MinicondaRoot"
}

if (-not (Test-Path $CondaExe)) {
    throw "conda.exe not found at $CondaExe"
}

# Accept ToS (non-interactive)
& $CondaExe tos accept --override-channels --channel https://repo.anaconda.com/pkgs/main 2>$null
& $CondaExe tos accept --override-channels --channel https://repo.anaconda.com/pkgs/r 2>$null
& $CondaExe tos accept --override-channels --channel https://repo.anaconda.com/pkgs/msys2 2>$null

# Prefer conda-forge for scientific stack
$pkgArgs = @(
    "-c", "conda-forge",
    "python=3.12",
    "xraylarch=2026.2.2",
    "pyside6=6.11.1",
    "matplotlib=3.10.9",
    "numpy=2.4.6",
    "scipy=1.17.1",
    "openpyxl"
)
$envExists = Test-Path -LiteralPath $EnvPrefix
if ($envExists -and -not (Test-Path -LiteralPath $PyExe)) {
    Write-Log "Env prefix exists but python.exe missing; removing broken prefix"
    & $CondaExe env remove -y -p $EnvPrefix 2>&1 | ForEach-Object { Write-Log "$_" }
    Remove-Item -LiteralPath $EnvPrefix -Recurse -Force -ErrorAction SilentlyContinue
    $envExists = Test-Path -LiteralPath $EnvPrefix
}
if ($Force -and $envExists) {
    Write-Log "Removing existing env (Force)"
    & $CondaExe env remove -y -p $EnvPrefix 2>&1 | ForEach-Object { Write-Log "$_" }
    $envExists = Test-Path -LiteralPath $EnvPrefix
}
# Re-install used to always `conda create`, which errors when the prefix
# already exists (v2.16.2). Update in place unless Force removed it.
# Use -p (prefix) not -n (name): an interrupted create may leave the folder
# without registering the env name, so `conda install -n smartlarch` fails.
if ($envExists) {
    Write-Log "Env prefix exists; conda install -p $EnvPrefix (not create)"
    $condaArgs = @("install", "-y", "-p", $EnvPrefix) + $pkgArgs
} else {
    Write-Log "Creating env at $EnvPrefix (xraylarch pyside6 matplotlib python=3.12)"
    $condaArgs = @("create", "-y", "-p", $EnvPrefix) + $pkgArgs
}

$condaOut = Join-Path $env:TEMP "smartLarch-conda-create-stdout.txt"
$condaErr = Join-Path $env:TEMP "smartLarch-conda-create-stderr.txt"
$p2 = Start-Process -FilePath $CondaExe -ArgumentList $condaArgs -Wait -PassThru -NoNewWindow `
    -RedirectStandardOutput $condaOut `
    -RedirectStandardError $condaErr
Write-Log "conda $($condaArgs[0]) exit=$($p2.ExitCode)"
Get-Content $condaOut -ErrorAction SilentlyContinue | ForEach-Object { Write-Log $_ }
Get-Content $condaErr -ErrorAction SilentlyContinue | ForEach-Object { Write-Log "ERR $_" }

if (-not (Test-EnvHealthy $PyExe)) {
    # Retry update if create partially succeeded
    Write-Log "Health check failed; trying conda install into env"
    $instArgs = @(
        "install", "-y", "-p", $EnvPrefix, "-c", "conda-forge",
        "xraylarch=2026.2.2", "pyside6=6.11.1", "matplotlib=3.10.9", "openpyxl"
    )
    $instOut = Join-Path $env:TEMP "smartLarch-conda-install-stdout.txt"
    $instErr = Join-Path $env:TEMP "smartLarch-conda-install-stderr.txt"
    Start-Process -FilePath $CondaExe -ArgumentList $instArgs -Wait -PassThru -NoNewWindow `
        -RedirectStandardOutput $instOut `
        -RedirectStandardError $instErr | Out-Null
    Get-Content $instOut -ErrorAction SilentlyContinue | ForEach-Object { Write-Log $_ }
    Get-Content $instErr -ErrorAction SilentlyContinue | ForEach-Object { Write-Log "ERR $_" }
}

# MCR-ALS (Analysis -> after PCA): pyMCR via pip -- required for full Analysis chain
if (-not (Install-Pymcr $PyExe)) {
    Write-Log "WARN: core packages OK but pymcr failed; continuing (MCR-ALS may be unavailable)"
}

# Excel .xlsx export (Tools -> Export): openpyxl -- conda list above covers fresh
# installs; this pip ensure covers envs created before openpyxl was added.
if (-not (Test-EnvHealthy $PyExe -RequireOpenpyxl)) {
    if (-not (Install-Openpyxl $PyExe)) {
        Write-Log "WARN: core packages OK but openpyxl failed; continuing (Excel export may be unavailable)"
    }
}

if (-not (Test-EnvHealthy $PyExe)) {
    Write-Log "FAILED health check for $PyExe"
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show(
        "Runtime install finished but import check failed.`n`nThe log is in the same folder as the installer:`n$Log",
        "smartLarch Platform",
        "OK",
        "Error"
    ) | Out-Null
    exit 1
}

$info = @{
    schema = "smartlarch-runtime/v1"
    python = $PyExe
    env_prefix = $EnvPrefix
    miniconda = $MinicondaRoot
    installed_at = (Get-Date).ToString("o")
    source = "miniconda+conda-forge"
    packages = @("python=3.12", "xraylarch", "pyside6", "matplotlib", "numpy", "scipy", "pymcr", "openpyxl")
} | ConvertTo-Json
Set-Content -Path $Marker -Value $info -Encoding UTF8
Write-Log "SUCCESS. Marker written to $Marker"
Write-Host ""
Write-Host "Runtime installed successfully."
Write-Host "Python: $PyExe"
Write-Host "Includes: xraylarch, PySide6, matplotlib, pymcr (MCR-ALS), openpyxl (Excel export)"
Write-Host "You can close this window and launch smartLarch Platform."
exit 0
