# Stable Start Menu shortcuts for smartLarch Platform + four web subprojects.
# Workspace section B.19: no version in .lnk name; update TargetPath IN PLACE (never delete+recreate).
#
# Grouped folder ONLY (Start Menu "All apps" shows one folder, not four scattered tiles):
#   %APPDATA%\Microsoft\Windows\Start Menu\Programs\smartLarch Platform\
# Flat leftovers under Programs\ are removed on each run.
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\Install-Shortcuts.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\Install-Shortcuts.ps1 -Tool easyxanes
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\Install-Shortcuts.ps1 -WhatIf

[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [ValidateSet("all", "platform", "easyxanes", "mex1", "xas-merged", "xrd-xas")]
    [string]$Tool = "all"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootFull = (Resolve-Path -LiteralPath $Root).Path
$shell = New-Object -ComObject WScript.Shell

function Get-HtmlAssocIcon {
    try {
        $progId = $null
        $uc = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey("Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.html\UserChoice")
        if ($uc) { $progId = $uc.GetValue("ProgId"); $uc.Close() }
        if ($progId) {
            $di = [Microsoft.Win32.Registry]::ClassesRoot.OpenSubKey("$progId\DefaultIcon")
            if ($di) { $ic = [string]$di.GetValue(""); $di.Close(); if ($ic) { return $ic } }
            $oc = [Microsoft.Win32.Registry]::ClassesRoot.OpenSubKey("$progId\shell\open\command")
            if ($oc) {
                $cmd = [string]$oc.GetValue(""); $oc.Close()
                if ($cmd -match '"([^"]+\.exe)"') { return "$($Matches[1]),0" }
                if ($cmd -match '(\S+\.exe)') { return "$($Matches[1]),0" }
            }
        }
    } catch {}
    return $null
}

function Get-LatestHtml([string]$Dir, [string]$Filter, [string]$Regex) {
    $cands = Get-ChildItem -LiteralPath $Dir -File -Filter $Filter -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match $Regex } |
        Sort-Object {
            if ($_.Name -match 'v(\d+)\.(\d+)\.(\d+)') {
                [version]::new([int]$Matches[1], [int]$Matches[2], [int]$Matches[3])
            } else { [version]'0.0.0' }
        } -Descending
    if (-not $cands -or $cands.Count -eq 0) { return $null }
    return $cands[0]
}

function Update-ShortcutInPlace {
    param(
        [Parameter(Mandatory = $true)][string]$LnkPath,
        [Parameter(Mandatory = $true)][string]$TargetPath,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [string]$Description = "",
        [string]$IconLocation = ""
    )
    $dir = Split-Path -Parent $LnkPath
    if (-not (Test-Path -LiteralPath $dir)) {
        if ($PSCmdlet.ShouldProcess($dir, "Create directory")) {
            New-Item -ItemType Directory -Force -Path $dir | Out-Null
        }
    }
    $existed = Test-Path -LiteralPath $LnkPath
    if ($PSCmdlet.ShouldProcess($LnkPath, $(if ($existed) { "Update TargetPath in place" } else { "Create shortcut" }))) {
        $sc = $shell.CreateShortcut($LnkPath)
        $sc.TargetPath = $TargetPath
        $sc.WorkingDirectory = $WorkingDirectory
        $sc.WindowStyle = 1
        $sc.Description = $Description
        if ($IconLocation) { $sc.IconLocation = $IconLocation }
        $sc.Save()
        Write-Host ("  {0}: {1}" -f $(if ($existed) { "Updated" } else { "Created" }), $LnkPath)
        Write-Host ("           -> {0}" -f $TargetPath)
    }
}

$HtmlIcon = Get-HtmlAssocIcon
$startRoot = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
$groupDir = Join-Path $startRoot "smartLarch Platform"
$desktop = [Environment]::GetFolderPath("Desktop")
$taskbar = Join-Path $env:APPDATA "Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar"
$doAll = ($Tool -eq "all")

# --- Qt client ---
if ($doAll -or $Tool -eq "platform") {
    Write-Host "=== smartLarch Platform ==="
    $bat = Join-Path $RootFull "Launch-smartLarch-Platform.bat"
    $ico = Join-Path $RootFull "source\app\assets\app_icon.ico"
    $icon = if (Test-Path -LiteralPath $ico) { $ico } else { "" }
    $desc = "smartLarch Platform - Larch EXAFS fitting"
    $name = "smartLarch Platform.lnk"
    Update-ShortcutInPlace -LnkPath (Join-Path $groupDir $name) -TargetPath $bat -WorkingDirectory $RootFull -Description $desc -IconLocation $icon
    # Desktop: update only if the user already has one (do not scatter a new tile)
    $deskLnk = Join-Path $desktop $name
    if (Test-Path -LiteralPath $deskLnk) {
        Update-ShortcutInPlace -LnkPath $deskLnk -TargetPath $bat -WorkingDirectory $RootFull -Description $desc -IconLocation $icon
    }
    $tb = Join-Path $taskbar $name
    $legacyTb = Join-Path $taskbar "Auto LarchUI.lnk"
    if (Test-Path -LiteralPath $tb) {
        Update-ShortcutInPlace -LnkPath $tb -TargetPath $bat -WorkingDirectory $RootFull -Description $desc -IconLocation $icon
    } elseif (Test-Path -LiteralPath $legacyTb) {
        Update-ShortcutInPlace -LnkPath $tb -TargetPath $bat -WorkingDirectory $RootFull -Description $desc -IconLocation $icon
        Remove-Item -LiteralPath $legacyTb -Force
        Write-Host "  Migrated taskbar pin: Auto LarchUI -> smartLarch Platform"
    }
    foreach ($legacy in @("Auto LarchUI.lnk")) {
        foreach ($d in @($startRoot, $desktop, $groupDir)) {
            $old = Join-Path $d $legacy
            if (Test-Path -LiteralPath $old) { Remove-Item -LiteralPath $old -Force; Write-Host "  Removed legacy: $old" }
        }
    }
}

function Install-WebTool {
    param(
        [string]$Key,
        [string]$SubDir,
        [string]$Filter,
        [string]$Regex,
        [string]$LnkName,
        [string]$Description
    )
    if (-not ($doAll -or $Tool -eq $Key)) { return }
    Write-Host "=== $LnkName ==="
    $dir = Join-Path $RootFull $SubDir
    $html = Get-LatestHtml -Dir $dir -Filter $Filter -Regex $Regex
    if (-not $html) {
        Write-Host "  Skip $LnkName : no $Filter under $dir (user zip has plugins only)"
        return
    }
    $target = $html.FullName
    $wd = $dir
    $desc = "$Description - $($html.Name)"
    Update-ShortcutInPlace -LnkPath (Join-Path $groupDir $LnkName) -TargetPath $target -WorkingDirectory $wd -Description $desc -IconLocation $HtmlIcon
    $desk = Join-Path $desktop $LnkName
    if (Test-Path -LiteralPath $desk) {
        Update-ShortcutInPlace -LnkPath $desk -TargetPath $target -WorkingDirectory $wd -Description $desc -IconLocation $HtmlIcon
    }
    $tb = Join-Path $taskbar $LnkName
    if (Test-Path -LiteralPath $tb) {
        Update-ShortcutInPlace -LnkPath $tb -TargetPath $target -WorkingDirectory $wd -Description $desc -IconLocation $HtmlIcon
    }
}

function Remove-FlatStartMenuLeftover([string]$LnkName) {
    $flat = Join-Path $startRoot $LnkName
    if (Test-Path -LiteralPath $flat) {
        if ($PSCmdlet.ShouldProcess($flat, "Remove flat Start Menu leftover (now in smartLarch Platform folder)")) {
            Remove-Item -LiteralPath $flat -Force
            Write-Host "  Removed flat leftover: $flat"
        }
    }
}

Install-WebTool -Key "easyxanes" -SubDir "easyxanes" -Filter "easyXANES_web_v*.html" `
    -Regex '^easyXANES_web_v(\d+)\.(\d+)\.(\d+)\.html$' `
    -LnkName "easyXAFS300+ Data Processor.lnk" `
    -Description "easyXAFS300+ Data Processor (Web)"

Install-WebTool -Key "xas-merged" -SubDir "xas-merged" -Filter "XAS_XES_merged_precheck_tools*.html" `
    -Regex 'v(\d+)\.(\d+)\.(\d+)' `
    -LnkName "XAS Merged Tools.lnk" `
    -Description "XAS Merged Tools (Web)"

Install-WebTool -Key "xrd-xas" -SubDir "xrd-xas" -Filter "XRD_XAS_Data_Processing_v*.html" `
    -Regex '^XRD_XAS_Data_Processing_v(\d+)\.(\d+)\.(\d+)\.html$' `
    -LnkName "XRD-XAS Data Processing.lnk" `
    -Description "XRD-XAS Data Processing (Web)"

Install-WebTool -Key "mex1" -SubDir "mex1" -Filter "MEX1_Data_Treatment_v*.html" `
    -Regex '^MEX1_Data_Treatment_v(\d+)\.(\d+)\.(\d+)\.html$' `
    -LnkName "MEX1 Data Treatment.lnk" `
    -Description "MEX1 Data Treatment (Web)"

# Start Menu All-apps list is Programs\*.lnk PLUS folder groups.
# After grouping, delete the old root tiles so they are not listed twice.
foreach ($n in @(
    "smartLarch Platform.lnk",
    "easyXAFS300+ Data Processor.lnk",
    "XAS Merged Tools.lnk",
    "MEX1 Data Treatment.lnk",
    "XRD-XAS Data Processing.lnk",
    "XAS-XRD Combined Tool.lnk"   # pre-plugin standalone tool (now xrd-xas)
)) { Remove-FlatStartMenuLeftover $n }

Write-Host ""
Write-Host "Done. Start Menu -> Programs -> smartLarch Platform  (five shortcuts inside that folder)"
Write-Host "  $groupDir"
Write-Host "  After a web-tool bump: re-run this script (or -Tool <name>)."
