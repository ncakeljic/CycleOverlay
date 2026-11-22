<#
.SYNOPSIS
  Baut den Windows-Installer von CycleOverlay (CycleOverlay-Setup-<Version>.exe).

.DESCRIPTION
  Auf einem Windows-PC im Projektordner ausführen:
      powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1

  Das Skript
    1. installiert bei Bedarf uv (Python-Paketverwaltung) und Inno Setup (Installer-Bauer, per winget),
    2. lädt ffmpeg/ffprobe (LGPL-Build) zum Mitliefern,
    3. führt die Tests aus (abschaltbar mit -SkipTests),
    4. bündelt die App mit PyInstaller nach dist\CycleOverlay\,
    5. prüft die gebündelte App mit „CycleOverlay.exe selftest“,
    6. baut den Installer nach packaging\windows\Output\.
#>
param(
    [switch]$SkipTests,
    [switch]$SkipFfmpeg
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # Downloads deutlich schneller
$Root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $Root

function Step($text) { Write-Host ""; Write-Host "==> $text" -ForegroundColor Cyan }

# --- Version aus dem Quellcode
$init = Get-Content "src\cycle_overlay\__init__.py" -Raw
if ($init -notmatch '__version__\s*=\s*"([^"]+)"') { throw "Version nicht gefunden" }
$Version = $Matches[1]
Write-Host "CycleOverlay $Version" -ForegroundColor Green

# --- 1. uv
Step "uv prüfen"
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "uv wird installiert ..."
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}
uv --version

# --- 1b. Inno Setup
Step "Inno Setup prüfen"
function Find-ISCC {
    $candidates = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
    )
    foreach ($c in $candidates) { if ($c -and (Test-Path $c)) { return $c } }
    $cmd = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}
$ISCC = Find-ISCC
if (-not $ISCC) {
    Write-Host "Inno Setup wird per winget installiert ..."
    winget install -e --id JRSoftware.InnoSetup --accept-package-agreements --accept-source-agreements
    $ISCC = Find-ISCC
    if (-not $ISCC) { throw "Inno Setup nicht gefunden. Bitte von https://jrsoftware.org/isdl.php installieren und erneut starten." }
}
Write-Host $ISCC

# --- 2. ffmpeg (LGPL-Build) zum Mitliefern
$FfDir = Join-Path $Root "packaging\windows\ffmpeg"
if (-not $SkipFfmpeg) {
    Step "ffmpeg bereitstellen"
    if ((Test-Path "$FfDir\ffmpeg.exe") -and (Test-Path "$FfDir\ffprobe.exe")) {
        Write-Host "vorhanden: $FfDir"
    } else {
        New-Item -ItemType Directory -Force -Path $FfDir | Out-Null
        $url = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-lgpl.zip"
        $zip = Join-Path $env:TEMP "cycleoverlay-ffmpeg.zip"
        $tmp = Join-Path $env:TEMP "cycleoverlay-ffmpeg"
        Write-Host "Lade $url"
        Invoke-WebRequest -Uri $url -OutFile $zip
        if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp }
        Unblock-File -Path $zip  # Internet-Markierung entfernen (sonst wird Smart App Control strenger)
        Expand-Archive -Path $zip -DestinationPath $tmp
        $bin = Get-ChildItem -Path $tmp -Recurse -Filter "ffmpeg.exe" | Select-Object -First 1
        if (-not $bin) { throw "ffmpeg.exe nicht im Download gefunden" }
        Copy-Item $bin.FullName $FfDir
        Copy-Item (Join-Path $bin.DirectoryName "ffprobe.exe") $FfDir
        $lic = Get-ChildItem -Path $tmp -Recurse -Filter "LICENSE*" | Select-Object -First 1
        if ($lic) { Copy-Item $lic.FullName (Join-Path $FfDir "FFMPEG-LICENSE.txt") }
        Remove-Item -Recurse -Force $tmp, $zip
    }
    Get-ChildItem -Path $FfDir -File | Unblock-File
    try {
        & "$FfDir\ffmpeg.exe" -hide_banner -version | Select-Object -First 1
        & "$FfDir\ffprobe.exe" -hide_banner -version | Select-Object -First 1
    } catch {
        Write-Warning "ffmpeg/ffprobe lassen sich auf diesem PC nicht starten (Smart App Control?): $_"
        Write-Warning "Die App weicht dann auf ein separat installiertes ffmpeg aus (winget install Gyan.FFmpeg)."
    }
} else {
    Write-Host "ffmpeg wird nicht mitgeliefert (-SkipFfmpeg) – die App sucht es dann im System."
}

# --- 3. Abhängigkeiten und Tests
Step "Python-Umgebung"
uv sync
New-Item -ItemType Directory -Force -Path "dist" | Out-Null
if (-not $SkipTests) {
    Step "Tests (Ergebnis auch in dist\tests_windows.txt)"
    uv run pytest -q -p no:cacheprovider 2>&1 | Tee-Object -FilePath "dist\tests_windows.txt" | Select-Object -Last 15
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Achtung: Einige Tests sind unter Windows fehlgeschlagen (siehe dist\tests_windows.txt)." -ForegroundColor Yellow
        Write-Host "Der Installer wird trotzdem gebaut; der Selbsttest unten prüft die gebündelte App." -ForegroundColor Yellow
    }
}

# --- 4. PyInstaller
Step "App bündeln (PyInstaller)"
if (Test-Path "dist\CycleOverlay") { Remove-Item -Recurse -Force "dist\CycleOverlay" }
uv run --with "pyinstaller>=6.6" pyinstaller --noconfirm --clean packaging\cycleoverlay.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller fehlgeschlagen" }
$Exe = Join-Path $Root "dist\CycleOverlay\CycleOverlay.exe"
if (-not (Test-Path $Exe)) { throw "dist\CycleOverlay\CycleOverlay.exe fehlt" }

# --- 5. Selbsttest der gebündelten App
Step "Selbsttest der gebündelten App"
$report = Join-Path $Root "dist\selftest.txt"
$p = Start-Process -FilePath $Exe -ArgumentList @("selftest", "--out", "`"$report`"") -Wait -PassThru -NoNewWindow
Get-Content $report | Write-Host
if ($p.ExitCode -ne 0) { throw "Selbsttest fehlgeschlagen – Details oben bzw. in dist\selftest.txt" }

# --- 6. Installer
Step "Installer bauen (Inno Setup)"
Push-Location "packaging\windows"
& $ISCC "/DAppVersion=$Version" "cycleoverlay.iss"
$code = $LASTEXITCODE
Pop-Location
if ($code -ne 0) { throw "Inno Setup fehlgeschlagen" }

$Setup = Join-Path $Root "packaging\windows\Output\CycleOverlay-Setup-$Version.exe"
Write-Host ""
Write-Host "Fertig: $Setup" -ForegroundColor Green
