$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$Version = '2.28'
$FixedAppId = '346MK1Nd8vpaSJy7EzQQT'
$InstallDir = Join-Path $env:LOCALAPPDATA 'ControlNexus'
$PayloadDir = Join-Path $PSScriptRoot 'app'
$DesktopShortcut = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Control Nexus.lnk'
$StartMenuDir = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
$StartMenuShortcut = Join-Path $StartMenuDir 'Control Nexus.lnk'
$LogPath = Join-Path $env:TEMP 'ControlNexus_install.log'
$DesktopLogPath = Join-Path ([Environment]::GetFolderPath('Desktop')) 'ControlNexus_install.log'
$DesktopFailurePath = Join-Path ([Environment]::GetFolderPath('Desktop')) 'ControlNexus_INSTALL_FAILED.txt'

function Write-Step([string]$Text) {
    Write-Host "`n==> $Text" -ForegroundColor Cyan
}

function Show-InstallerMessage([string]$Title, [string]$Message, [int]$Icon = 64) {
    try {
        $shell = New-Object -ComObject WScript.Shell
        [void]$shell.Popup($Message, 0, $Title, $Icon)
    } catch {
        Write-Host $Message
    }
}

function Save-InstallerLogCopy {
    try {
        if (Test-Path $LogPath) { Copy-Item $LogPath $DesktopLogPath -Force }
    } catch {}
}

function Stop-ExistingBackend {
    param([string]$Root)
    $pidFile = Join-Path $Root '.runtime_control\windows_backend.pid'
    if (Test-Path $pidFile) {
        try {
            $backendPid = [int](Get-Content $pidFile -Raw).Trim()
            if ($backendPid -gt 0) { Stop-Process -Id $backendPid -Force -ErrorAction SilentlyContinue }
        } catch {}
    }
}

function Resolve-Python {
    try {
        $out = & py -3 -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $out -and (Test-Path $out.Trim())) { return $out.Trim() }
    } catch {}
    try {
        $out = & python -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $out -and (Test-Path $out.Trim())) { return $out.Trim() }
    } catch {}
    $roots = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Python'),
        (Join-Path $env:ProgramFiles 'Python*'),
        (Join-Path ${env:ProgramFiles(x86)} 'Python*')
    )
    foreach ($root in $roots) {
        if (-not $root) { continue }
        try {
            $candidate = Get-ChildItem -Path $root -Filter python.exe -Recurse -ErrorAction SilentlyContinue |
                Where-Object { $_.FullName -notmatch '\\.venv\\' } |
                Sort-Object FullName -Descending |
                Select-Object -First 1
            if ($candidate) { return $candidate.FullName }
        } catch {}
    }
    return $null
}

function Save-UserData([string]$Root, [string]$BackupRoot) {
    New-Item -ItemType Directory -Path $BackupRoot -Force | Out-Null
    $files = @(
        '.env',
        'user_presets.json',
        'rise_fall_presets.json',
        'rise_fall_session.json',
        'rise_fall_trades.csv',
        'trades_v2.csv',
        'web_session.json',
        'candlestick_events.csv',
        'ensemble_decisions.csv',
        'ai_optimizer.json',
        'ai_optimizer_history.json'
    )
    foreach ($name in $files) {
        $src = Join-Path $Root $name
        if (Test-Path $src) { Copy-Item $src (Join-Path $BackupRoot $name) -Force }
    }
    foreach ($folder in @('presets','session_diagnostics','strategy_lab_runs','rise_fall_lab_runs','support_reports','ai_optimizer_runs')) {
        $src = Join-Path $Root $folder
        if (Test-Path $src) { Copy-Item $src (Join-Path $BackupRoot $folder) -Recurse -Force }
    }
}

function Restore-UserData([string]$BackupRoot, [string]$Root) {
    if (-not (Test-Path $BackupRoot)) { return }
    Get-ChildItem $BackupRoot -Force | ForEach-Object {
        $dest = Join-Path $Root $_.Name
        if ($_.PSIsContainer) {
            Copy-Item $_.FullName $dest -Recurse -Force
        } else {
            Copy-Item $_.FullName $dest -Force
        }
    }
}

function Force-AppId([string]$Root) {
    $envPath = Join-Path $Root '.env'
    if (Test-Path $envPath) {
        $envLines = Get-Content $envPath
        $found = $false
        $updated = foreach ($line in $envLines) {
            if ($line -match '^\s*DERIV_APP_ID=') {
                $found = $true
                "DERIV_APP_ID=$FixedAppId"
            } else {
                $line
            }
        }
        if (-not $found) { $updated += "DERIV_APP_ID=$FixedAppId" }
        Set-Content -Path $envPath -Value $updated -Encoding ASCII
    } else {
        # The installer does not embed a user's .env. Create a clean local one
        # containing only the non-secret fixed App ID; credentials can then be
        # saved through the Control Nexus UI.
        Set-Content -Path $envPath -Value "DERIV_APP_ID=$FixedAppId" -Encoding ASCII
    }
}

try {
    Start-Transcript -Path $LogPath -Force | Out-Null
    Write-Host "DR EXPLOIT CONTROL NEXUS V$Version - WINDOWS INSTALLER" -ForegroundColor Magenta
    Write-Host "Fixed Deriv App ID: $FixedAppId" -ForegroundColor DarkGray
    Write-Host 'This installs Control Nexus for the current Windows user.'

    if (-not (Test-Path $PayloadDir)) { throw 'Installer payload is missing.' }

    Write-Step 'Preparing the application folder'
    $backupRoot = Join-Path $env:TEMP ('ControlNexus_user_data_' + [guid]::NewGuid().ToString('N'))
    if (Test-Path $InstallDir) {
        Stop-ExistingBackend -Root $InstallDir
        Save-UserData -Root $InstallDir -BackupRoot $backupRoot
        Remove-Item $InstallDir -Recurse -Force
    }
    New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
    Copy-Item (Join-Path $PayloadDir '*') $InstallDir -Recurse -Force
    Restore-UserData -BackupRoot $backupRoot -Root $InstallDir
    Remove-Item $backupRoot -Recurse -Force -ErrorAction SilentlyContinue
    Force-AppId -Root $InstallDir

    Write-Step 'Checking Python 3'
    $python = Resolve-Python
    if (-not $python) {
        $winget = Get-Command winget -ErrorAction SilentlyContinue
        if ($winget) {
            Write-Host 'Python was not found. Installing Python 3.13 with Windows Package Manager...'
            & winget install --id Python.Python.3.13 -e --scope user --silent --accept-package-agreements --accept-source-agreements
            if ($LASTEXITCODE -ne 0) {
                Write-Host 'User-scope Python install did not complete; trying the standard Winget install...' -ForegroundColor Yellow
                & winget install --id Python.Python.3.13 -e --silent --accept-package-agreements --accept-source-agreements
            }
            Start-Sleep -Seconds 2
            $python = Resolve-Python
        }
    }
    if (-not $python) {
        throw 'Python 3 could not be installed automatically. Install Python 3.11 or newer, then run this installer again.'
    }
    Write-Host "Using Python: $python"

    Write-Step 'Creating the private Control Nexus Python environment'
    $venvPython = Join-Path $InstallDir '.venv\Scripts\python.exe'
    & $python -m venv (Join-Path $InstallDir '.venv')
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $venvPython)) { throw 'Could not create the application Python environment.' }

    Write-Step 'Installing backend requirements'
    & $venvPython -m pip install --disable-pip-version-check --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw 'Could not update pip.' }
    & $venvPython -m pip install --disable-pip-version-check -r (Join-Path $InstallDir 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Could not install the Control Nexus backend requirements.' }

    Write-Step 'Preparing data folders and checking the backend'
    foreach ($folder in @('session_diagnostics','strategy_lab_runs','rise_fall_lab_runs','support_reports','ai_optimizer_runs','.runtime_control','presets')) {
        New-Item -ItemType Directory -Path (Join-Path $InstallDir $folder) -Force | Out-Null
    }
    & $venvPython -m py_compile `
        (Join-Path $InstallDir 'bot.py') `
        (Join-Path $InstallDir 'web_ui.py') `
        (Join-Path $InstallDir 'rise_fall_bot.py') `
        (Join-Path $InstallDir 'ai_optimizer.py') `
        (Join-Path $InstallDir 'control_nexus_launcher.py')
    if ($LASTEXITCODE -ne 0) { throw 'The installed backend failed its Python validation check.' }

    Write-Step 'Creating Desktop and Start Menu shortcuts'
    $shell = New-Object -ComObject WScript.Shell
    foreach ($shortcutPath in @($DesktopShortcut, $StartMenuShortcut)) {
        $shortcut = $shell.CreateShortcut($shortcutPath)
        $shortcut.TargetPath = Join-Path $InstallDir 'ControlNexus.exe'
        $shortcut.WorkingDirectory = $InstallDir
        $shortcut.Description = "Dr Exploit Control Nexus V$Version"
        $shortcut.IconLocation = (Join-Path $InstallDir 'ControlNexus.ico') + ',0'
        $shortcut.Save()
    }

    Write-Step 'Adding Windows uninstall support'
    $uninstallScript = Join-Path $InstallDir 'Uninstall-ControlNexus.ps1'
    @'
$ErrorActionPreference = 'SilentlyContinue'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$pidFile = Join-Path $root '.runtime_control\windows_backend.pid'
if (Test-Path $pidFile) {
    try { $pid = [int](Get-Content $pidFile -Raw).Trim(); if ($pid -gt 0) { Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue } } catch {}
}
Remove-Item (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Control Nexus.lnk') -Force -ErrorAction SilentlyContinue
Remove-Item (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Control Nexus.lnk') -Force -ErrorAction SilentlyContinue
Remove-Item 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\ControlNexus' -Recurse -Force -ErrorAction SilentlyContinue
$escaped = $root.Replace('''','''''')
Start-Process powershell.exe -WindowStyle Hidden -ArgumentList "-NoProfile -Command Start-Sleep -Seconds 2; Remove-Item -LiteralPath '$escaped' -Recurse -Force"
'@ | Set-Content -Path $uninstallScript -Encoding UTF8

    $uninstallKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\ControlNexus'
    New-Item $uninstallKey -Force | Out-Null
    New-ItemProperty $uninstallKey -Name DisplayName -Value 'Dr Exploit Control Nexus' -PropertyType String -Force | Out-Null
    New-ItemProperty $uninstallKey -Name DisplayVersion -Value $Version -PropertyType String -Force | Out-Null
    New-ItemProperty $uninstallKey -Name Publisher -Value 'Dr Exploit' -PropertyType String -Force | Out-Null
    New-ItemProperty $uninstallKey -Name InstallLocation -Value $InstallDir -PropertyType String -Force | Out-Null
    New-ItemProperty $uninstallKey -Name DisplayIcon -Value (Join-Path $InstallDir 'ControlNexus.ico') -PropertyType String -Force | Out-Null
    $uninstallCommand = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "' + $uninstallScript + '"'
    New-ItemProperty $uninstallKey -Name UninstallString -Value $uninstallCommand -PropertyType String -Force | Out-Null
    New-ItemProperty $uninstallKey -Name NoModify -Value 1 -PropertyType DWord -Force | Out-Null
    New-ItemProperty $uninstallKey -Name NoRepair -Value 1 -PropertyType DWord -Force | Out-Null

    Write-Step 'Installation complete'
    Write-Host "Installed to: $InstallDir" -ForegroundColor Green
    Write-Host 'Desktop shortcut: Control Nexus' -ForegroundColor Green
    Write-Host 'Start Menu shortcut: Control Nexus' -ForegroundColor Green
    try { Stop-Transcript | Out-Null } catch {}
    Save-InstallerLogCopy
    Remove-Item $DesktopFailurePath -Force -ErrorAction SilentlyContinue
    Show-InstallerMessage 'Control Nexus Setup' "Installation completed successfully.`n`nInstalled to:`n$InstallDir`n`nA Desktop shortcut and Start Menu shortcut were created.`n`nControl Nexus will start when you click OK." 64
    Start-Process (Join-Path $InstallDir 'ControlNexus.exe')
    exit 0
}
catch {
    $errorMessage = $_.Exception.Message
    $errorDetails = ($_ | Format-List * -Force | Out-String)
    Write-Host "`n============================================================" -ForegroundColor Red
    Write-Host 'CONTROL NEXUS INSTALLATION FAILED' -ForegroundColor Red
    Write-Host '============================================================' -ForegroundColor Red
    Write-Host "Reason: $errorMessage" -ForegroundColor Red
    try { Add-Content -Path $LogPath -Value "`n===== INSTALLATION FAILURE DETAILS =====`n$errorDetails" -Encoding UTF8 } catch {}
    try { Stop-Transcript | Out-Null } catch {}
    Save-InstallerLogCopy
    try {
        @"
CONTROL NEXUS INSTALLATION FAILED

Reason:
$errorMessage

Full diagnostic details:
$errorDetails

Main installer log:
$DesktopLogPath
"@ | Set-Content -Path $DesktopFailurePath -Encoding UTF8
    } catch {}
    Show-InstallerMessage 'Control Nexus Setup - Installation Failed' "Control Nexus could not be installed.`n`nReason:`n$errorMessage`n`nDiagnostic files were saved to your Desktop." 16
    Write-Host "`nPress ENTER to close this installer after you have read the error..." -ForegroundColor Yellow
    try { [void](Read-Host) } catch { Start-Sleep -Seconds 60 }
    exit 1
}
