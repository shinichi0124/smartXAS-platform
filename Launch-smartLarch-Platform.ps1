# Launch smartLarch Platform (end-user).
# 1) Prefer managed runtime: %LOCALAPPDATA%\smartLarch\Miniconda3\envs\smartlarch
# 2) Else well-known xraylarch / conda installs
# 3) Else first-run Install-Runtime.ps1 (download Miniconda + conda-forge packages)
#
# GUI uses pythonw; first-run install opens a visible console window.
#
# ASCII only. Windows PowerShell 5.1 reads .ps1 as the system ANSI code page
# unless a UTF-8 BOM is present. An en-dash in "1-2 GB" (UTF-8 E2 80 93) is
# decoded on English Windows (CP1252) as a stray quote, and the script dies
# at parse time -- before any log is written (v0.35.13 customer first-run).

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
# Logs sit next to this launcher (same folder as the .bat / extracted zip).
$BootLog = Join-Path $Root "smartLarch-launch.log"
$InstallLog = Join-Path $Root "smartLarch-runtime-install.log"
$ErrFile = Join-Path $Root "smartLarch-startup-error.txt"
$InstallRuntime = Join-Path $Root "Install-Runtime.ps1"

function Write-Boot([string]$msg) {
    try {
        $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
        Add-Content -LiteralPath $BootLog -Value $line -Encoding UTF8
    } catch { }
}

function Show-LaunchError([string]$msg) {
    # MessageBox when possible; ALWAYS echo to the console too -- the .bat
    # pauses on failure so the text stays readable (v0.35.7).
    $full = $msg + "`n`nLog (same folder as this launcher):`n" + $BootLog
    if (Test-Path -LiteralPath $InstallLog) {
        $full = $full + "`n`nRuntime install log:`n" + $InstallLog
    }
    Write-Host ""
    Write-Host "[smartLarch] $full" -ForegroundColor Red
    try {
        Add-Type -AssemblyName System.Windows.Forms
        [System.Windows.Forms.MessageBox]::Show(
            $full, "smartLarch Platform", "OK", "Error"
        ) | Out-Null
    } catch { }
}

function Get-ManagedCandidates {
    $base = Join-Path $env:LOCALAPPDATA "smartLarch"
    $envRoot = Join-Path $base "Miniconda3\envs\smartlarch"
    @(
        (Join-Path $envRoot "pythonw.exe"),
        (Join-Path $envRoot "python.exe")
    )
}

function Get-WellKnownCandidates {
    $list = [System.Collections.Generic.List[string]]::new()
    foreach ($p in (Get-ManagedCandidates)) {
        if (-not $list.Contains($p)) { [void]$list.Add($p) }
    }
    foreach ($p in @(
        "C:\ProgramData\xraylarch\pythonw.exe",
        "C:\ProgramData\xraylarch\python.exe",
        "$env:USERPROFILE\xraylarch\pythonw.exe",
        "$env:USERPROFILE\xraylarch\python.exe",
        "$env:LOCALAPPDATA\xraylarch\pythonw.exe",
        "$env:LOCALAPPDATA\xraylarch\python.exe",
        "$env:USERPROFILE\.conda\envs\larch\pythonw.exe",
        "$env:USERPROFILE\.conda\envs\larch\python.exe",
        "$env:USERPROFILE\anaconda3\envs\larch\pythonw.exe",
        "$env:USERPROFILE\anaconda3\envs\larch\python.exe",
        "$env:USERPROFILE\miniconda3\envs\larch\pythonw.exe",
        "$env:USERPROFILE\miniconda3\envs\larch\python.exe",
        "C:\ProgramData\anaconda3\envs\larch\pythonw.exe",
        "C:\ProgramData\anaconda3\envs\larch\python.exe"
    )) {
        if (-not $list.Contains($p)) { [void]$list.Add($p) }
    }
    $envTxt = Join-Path $env:USERPROFILE ".conda\environments.txt"
    if (Test-Path $envTxt) {
        Get-Content $envTxt -ErrorAction SilentlyContinue | ForEach-Object {
            $line = $_.Trim()
            if (-not $line -or $line.StartsWith("#")) { return }
            foreach ($exe in @("pythonw.exe", "python.exe")) {
                $full = Join-Path $line $exe
                if (-not $list.Contains($full)) { [void]$list.Add($full) }
            }
        }
    }
    return $list
}

function Resolve-Python {
    # v2.16.3 only checked that python.exe existed. University PCs often have
    # Anaconda/base listed in .conda\environments.txt; that Python has neither
    # larch nor PySide6, so the GUI died with ModuleNotFoundError: PySide6
    # and never offered first-run install (v2.16.4 customer screenshot).
    $tried = @()
    $unhealthy = @()
    foreach ($c in (Get-WellKnownCandidates)) {
        $tried += $c
        if (-not (Test-Path -LiteralPath $c)) { continue }
        if (Test-LarchImports $c) {
            Write-Boot "Healthy python: $c"
            return @{ Py = $c; Tried = $tried; Unhealthy = $unhealthy }
        }
        Write-Boot "Skip (missing larch/PySide6): $c"
        $unhealthy += $c
    }
    return @{ Py = $null; Tried = $tried; Unhealthy = $unhealthy }
}

function Invoke-RuntimeInstall {
    param([switch]$Yes)
    if (-not (Test-Path -LiteralPath $InstallRuntime)) {
        throw "Install-Runtime.ps1 not found next to launcher: $InstallRuntime"
    }
    Write-Boot "Starting first-run runtime install: $InstallRuntime"
    $arg = @(
        "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", $InstallRuntime
    )
    if ($Yes) { $arg += "-Yes" }
    # Visible console so user sees progress (can take 10-30 min)
    $p = Start-Process -FilePath "powershell.exe" -ArgumentList $arg -Wait -PassThru
    Write-Boot "Install-Runtime exit=$($p.ExitCode)"
    return $p.ExitCode
}

function Test-LarchImports([string]$PythonPath) {
    $console = $PythonPath
    if ($console -like "*pythonw.exe") {
        $tryC = $console -replace "pythonw\.exe$", "python.exe"
        if (Test-Path -LiteralPath $tryC) { $console = $tryC }
    }
    if (-not (Test-Path -LiteralPath $console)) { return $false }
    $oldPath = $env:PATH
    $tag = "{0}_{1}" -f $PID, [guid]::NewGuid().ToString("N")
    $out = Join-Path $env:TEMP "sl_launch_imp_out_$tag.txt"
    $err = Join-Path $env:TEMP "sl_launch_imp_err_$tag.txt"
    try {
        $envRoot = Split-Path -Parent $console
        $prepend = @(
            (Join-Path $envRoot "Library\bin"),
            (Join-Path $envRoot "Library\usr\bin"),
            (Join-Path $envRoot "Scripts"),
            $envRoot
        ) | Where-Object { $_ -and (Test-Path $_) }
        if ($prepend) {
            $env:PATH = ($prepend + @($oldPath)) -join ";"
        }
        # PS 5.1 joins an -ArgumentList ARRAY with spaces WITHOUT re-quoting,
        # so python received bare "import" (SyntaxError) and a healthy runtime
        # was misjudged as broken -> endless first-run install prompt (v2.16.12).
        # Pass ONE pre-quoted string; Start-Process uses it verbatim.
        $p = Start-Process -FilePath $console -ArgumentList "-c `"import larch, PySide6; print('OK')`"" -Wait -PassThru -NoNewWindow `
            -RedirectStandardOutput $out -RedirectStandardError $err
        $txt = Get-Content $out -Raw -ErrorAction SilentlyContinue
        $errTxt = Get-Content $err -Raw -ErrorAction SilentlyContinue
        if ($p.ExitCode -ne 0 -or $txt -notmatch "OK") {
            $snippet = ""
            if ($errTxt) { $snippet = $errTxt.Trim() }
            elseif ($txt) { $snippet = $txt.Trim() }
            if ($snippet.Length -gt 300) { $snippet = $snippet.Substring(0, 300) }
            Write-Boot "Import check failed for $console exit=$($p.ExitCode) $snippet"
            return $false
        }
        return $true
    } catch {
        Write-Boot "Import check exception for $console : $_"
        return $false
    } finally {
        $env:PATH = $oldPath
        Remove-Item -LiteralPath $out, $err -Force -ErrorAction SilentlyContinue
    }
}

function Install-PlatformShortcut {
    # Start Menu (always) + Desktop if missing. Never fail the launch.
    # Users could not find the app after first-run because shortcuts were
    # a separate optional script not even packed in the zip (v2.16.2).
    try {
        $startRoot = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
        $groupDir = Join-Path $startRoot "smartLarch Platform"
        if (-not (Test-Path -LiteralPath $groupDir)) {
            New-Item -ItemType Directory -Force -Path $groupDir | Out-Null
        }
        $bat = Join-Path $Root "Launch-smartLarch-Platform.bat"
        if (-not (Test-Path -LiteralPath $bat)) { return }
        $ico = Join-Path $Root "source\app\assets\app_icon.ico"
        $shell = New-Object -ComObject WScript.Shell
        $lnk = Join-Path $groupDir "smartLarch Platform.lnk"
        $sc = $shell.CreateShortcut($lnk)
        $sc.TargetPath = $bat
        $sc.WorkingDirectory = $Root
        $sc.WindowStyle = 1
        $sc.Description = "smartLarch Platform"
        if (Test-Path -LiteralPath $ico) { $sc.IconLocation = $ico }
        $sc.Save()
        Write-Boot "Start Menu shortcut: $lnk"
        $desk = Join-Path ([Environment]::GetFolderPath("Desktop")) "smartLarch Platform.lnk"
        if (-not (Test-Path -LiteralPath $desk)) {
            $sc2 = $shell.CreateShortcut($desk)
            $sc2.TargetPath = $bat
            $sc2.WorkingDirectory = $Root
            $sc2.WindowStyle = 1
            $sc2.Description = "smartLarch Platform"
            if (Test-Path -LiteralPath $ico) { $sc2.IconLocation = $ico }
            $sc2.Save()
            Write-Boot "Desktop shortcut: $desk"
        }
    } catch {
        Write-Boot "Shortcut install skipped: $_"
    }
}

Write-Boot "Launcher start Root=$Root"

function Invoke-InstallThenResolve {
    param([string]$FailHint)
    try {
        $code = Invoke-RuntimeInstall -Yes
    } catch {
        Write-Boot "Install exception: $_"
        Show-LaunchError "$_"
        exit 1
    }
    if ($code -ne 0) {
        Show-LaunchError ("Runtime install did not complete (exit {0}).`n{1}" -f $code, $FailHint)
        exit $code
    }
    $after = Resolve-Python
    if (-not $after.Py) {
        Show-LaunchError ("Install finished but still cannot import larch and PySide6.`n{0}" -f $FailHint)
        exit 1
    }
    return $after
}

$resolved = Resolve-Python
$py = $resolved.Py
if (-not $py) {
    $managedPresent = $false
    foreach ($c in (Get-ManagedCandidates)) {
        if (Test-Path -LiteralPath $c) { $managedPresent = $true; break }
    }
    $resumeHint = "If the network dropped, reconnect and run the launcher again to resume."
    if ($managedPresent) {
        Write-Boot "Managed runtime incomplete (imports failed). Resuming install."
        $resolved = Invoke-InstallThenResolve -FailHint $resumeHint
        $py = $resolved.Py
    } else {
        Write-Boot "No healthy runtime found. Offering install. Tried:`n$($resolved.Tried -join "`n")"
        if ($resolved.Unhealthy -and $resolved.Unhealthy.Count) {
            Write-Boot "Existing Python(s) skipped (no larch+PySide6): $($resolved.Unhealthy -join ', ')"
        }
        $wantInstall = $false
        $promptExtra = ""
        if ($resolved.Unhealthy -and $resolved.Unhealthy.Count) {
            $promptExtra = "A Python was found but it cannot import larch and PySide6 (Anaconda/base is not enough).`n`n"
        }
        try {
            Add-Type -AssemblyName System.Windows.Forms
            $wantInstall = [System.Windows.Forms.MessageBox]::Show(
                ($promptExtra + "No xraylarch + PySide6 environment found.`n`nInstall a private runtime now?`n(About 1-2 GB, needs internet, 10-30 min)`n`nInstall location:`n$env:LOCALAPPDATA\smartLarch\Miniconda3\envs\smartlarch"),
                "smartLarch Platform - first run",
                [System.Windows.Forms.MessageBoxButtons]::YesNo,
                [System.Windows.Forms.MessageBoxIcon]::Question
            ) -eq [System.Windows.Forms.DialogResult]::Yes
        } catch {
            Write-Host ""
            Write-Host "[smartLarch] No xraylarch + PySide6 environment found." -ForegroundColor Yellow
            if ($promptExtra) { Write-Host $promptExtra.Trim() }
            Write-Host "Install a private runtime now? (~1-2 GB, needs internet, 10-30 min)"
            Write-Host "Target: $env:LOCALAPPDATA\smartLarch\Miniconda3\envs\smartlarch"
            $answer = Read-Host "Type Y to install, anything else to cancel"
            $wantInstall = ($answer -match '^(?i)y(es)?$')
        }
        if (-not $wantInstall) {
            Show-LaunchError "Cannot start without xraylarch + PySide6.`nRe-run the launcher and choose Yes, or install manually: https://xraylarch.github.io/"
            exit 1
        }
        $resolved = Invoke-InstallThenResolve -FailHint $resumeHint
        $py = $resolved.Py
    }
}

Write-Boot "Resolved python: $py"
if (-not (Test-LarchImports $py)) {
    Show-LaunchError "Python was found but still cannot import larch and PySide6:`n$py`n`nIf the network dropped, reconnect and run the launcher again to resume."
    exit 1
}
Install-PlatformShortcut

$pyConsole = if ($py -like "*pythonw.exe") { $py -replace "pythonw\.exe$", "python.exe" } else { $py }
if (-not (Test-Path $pyConsole)) { $pyConsole = $py }
$pyGui = $py
if ($pyGui -notlike "*pythonw.exe") {
    $tryW = $pyGui -replace "python\.exe$", "pythonw.exe"
    if (Test-Path $tryW) { $pyGui = $tryW }
}

$isCli = $false
foreach ($a in $args) {
    if ("$a" -match "^(--)?(validate|cli-fit|help|h)$" -or "$a" -eq "-h" -or "$a" -eq "--help") {
        $isCli = $true
    }
}

$Source = Join-Path $Root "source"
$EnvRoot = Split-Path -Parent $pyConsole
function Set-CondaLikeEnv {
    $env:CONDA_PREFIX = $EnvRoot
    $env:CONDA_DEFAULT_ENV = if ($EnvRoot -match '[\\/]envs[\\/]([^\\/]+)$') { $Matches[1] } else { "base" }
    $prepend = @(
        (Join-Path $EnvRoot "Library\bin"),
        (Join-Path $EnvRoot "Library\usr\bin"),
        (Join-Path $EnvRoot "Scripts"),
        $EnvRoot
    ) | Where-Object { $_ -and (Test-Path $_) }
    $pathParts = ($env:PATH -split ';' | Where-Object { $_ -and ($prepend -notcontains $_) })
    $env:PATH = ($prepend + $pathParts) -join ';'

    $qtPlugins = @(
        (Join-Path $EnvRoot "Library\lib\qt6\plugins"),
        (Join-Path $EnvRoot "Library\plugins"),
        (Join-Path $EnvRoot "Lib\site-packages\PySide6\plugins")
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($qtPlugins) {
        $env:QT_PLUGIN_PATH = $qtPlugins
        $plat = Join-Path $qtPlugins "platforms"
        if (Test-Path $plat) { $env:QT_QPA_PLATFORM_PLUGIN_PATH = $plat }
    }
    $env:QT_API = "pyside6"
}

Set-CondaLikeEnv
Write-Boot "pyGui=$pyGui envRoot=$EnvRoot QT_PLUGIN_PATH=$($env:QT_PLUGIN_PATH) source=$Source cli=$isCli"

if ($isCli) {
    Set-Location $Source
    & $pyConsole -m app.main @args
    exit $LASTEXITCODE
}

try {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $pyGui
    $argList = @("-m", "app.main") + @($args)
    $psi.Arguments = ($argList | ForEach-Object {
        $s = "$_"
        if ($s -match '[\s"]') { '"' + ($s -replace '"', '\"') + '"' } else { $s }
    }) -join " "
    $psi.WorkingDirectory = $Source
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $p = [System.Diagnostics.Process]::Start($psi)
    Write-Boot "Started PID=$($p.Id)"
    Start-Sleep -Seconds 3
    if ($p.HasExited) {
        if ($p.ExitCode -eq 0) {
            # Clean fast exit = single-instance handoff (2nd launch raised the running window).
            Write-Boot "Process exited cleanly within 3s (single-instance handoff or fast quit)"
            exit 0
        }
        $hex = "{0:X8}" -f ([uint32]($p.ExitCode -band 0xFFFFFFFF))
        Write-Boot "Process exited early code=$($p.ExitCode) (hex=0x$hex)"
        $psi2 = New-Object System.Diagnostics.ProcessStartInfo
        $psi2.FileName = $pyConsole
        $psi2.Arguments = "-m app.main"
        $psi2.WorkingDirectory = $Source
        $psi2.UseShellExecute = $false
        $psi2.RedirectStandardError = $true
        $psi2.RedirectStandardOutput = $true
        $psi2.CreateNoWindow = $false
        $proc = [System.Diagnostics.Process]::Start($psi2)
        $outTask = $proc.StandardOutput.ReadToEndAsync()
        $errTask = $proc.StandardError.ReadToEndAsync()
        $exited = $proc.WaitForExit(8000)
        if (-not $exited) {
            Write-Boot "Fallback console GUI alive after 8s (PID=$($proc.Id))"
            exit 0
        }
        $stdout = $outTask.Result
        $stderr = $errTask.Result
        if ($proc.ExitCode -eq 0) { exit 0 }
        $msg = "stdout:`n$stdout`nstderr:`n$stderr`nexit=$($proc.ExitCode)"
        Set-Content -LiteralPath $ErrFile -Value $msg -Encoding UTF8
        Write-Boot $msg
        Show-LaunchError "smartLarch Platform failed to stay open.`nSee:`n$ErrFile`n`n$stderr"
        exit 1
    }
} catch {
    Write-Boot "Start-Process exception: $_"
    Show-LaunchError "$_"
    exit 1
}

exit 0
