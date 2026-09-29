#Requires -RunAsAdministrator
<#
Installs the LM360 custom display driver:
  - pip installs Python dependencies
  - installs the PawnIO kernel driver (LibreHardwareMonitorLib needs it for CPU sensor access)
  - disables DeepCool's factory app autostart
  - registers a hidden, highest-privilege logon Task Scheduler task running pc_display.py
#>

param(
    [switch]$SkipPawnIO,
    [switch]$SkipDisableVendorAutostart
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$TaskName = "LM360CustomDisplay"

function Test-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-Admin)) {
    Write-Error "Run this script from an elevated (Administrator) PowerShell prompt."
    exit 1
}

Write-Host "== Installing Python dependencies =="
# Antivirus TLS-scanning (e.g. Avast Web Shield) intercepts HTTPS with its own root cert.
# Windows trusts it system-wide, but pip/Python use their own CA bundle and don't - point
# pip at it explicitly so package downloads don't fail with a cert verification error.
$avastCert = "C:\ProgramData\Avast Software\Avast\wscert.pem"
if (Test-Path $avastCert) {
    Write-Host "Detected Avast TLS interception, using its root cert for pip"
    $env:PIP_CERT = $avastCert
}
python -m pip install --upgrade pip
python -m pip install -r (Join-Path $ScriptDir "requirements.txt")

if (-not $SkipPawnIO) {
    Write-Host "== Installing PawnIO kernel driver =="
    $pawnioUrl = "https://github.com/namazso/PawnIO.Setup/releases/latest/download/PawnIO_setup.exe"
    $pawnioInstaller = Join-Path $env:TEMP "PawnIO_setup.exe"
    Invoke-WebRequest -Uri $pawnioUrl -OutFile $pawnioInstaller
    Start-Process -FilePath $pawnioInstaller -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART" -Wait
    Remove-Item $pawnioInstaller -ErrorAction SilentlyContinue
}
else {
    Write-Host "Skipping PawnIO install (-SkipPawnIO)"
}

if (-not $SkipDisableVendorAutostart) {
    Write-Host "== Disabling DeepCool factory app autostart =="
    $runKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
    if (Test-Path $runKey) {
        Get-Item -Path $runKey | Select-Object -ExpandProperty Property | Where-Object { $_ -match "DeepCool" } | ForEach-Object {
            Write-Host "Removing autostart entry: $_"
            Remove-ItemProperty -Path $runKey -Name $_ -ErrorAction SilentlyContinue
        }
    }
    Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object { $_.TaskName -match "DeepCool" } | ForEach-Object {
        Write-Host "Disabling scheduled task: $($_.TaskName)"
        Disable-ScheduledTask -TaskName $_.TaskName -TaskPath $_.TaskPath -ErrorAction SilentlyContinue | Out-Null
    }
}
else {
    Write-Host "Skipping vendor autostart removal (-SkipDisableVendorAutostart)"
}

Write-Host "== Registering scheduled task =="
$pythonPath = (Get-Command python).Source
$scriptPath = Join-Path $ScriptDir "pc_display.py"

$action = New-ScheduledTaskAction -Execute $pythonPath -Argument "`"$scriptPath`"" -WorkingDirectory $ScriptDir
$trigger = New-ScheduledTaskTrigger -AtLogOn
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -Hidden -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Days 0)

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings | Out-Null

Write-Host "== Starting task now =="
Start-ScheduledTask -TaskName $TaskName

Write-Host ""
Write-Host "Done. '$TaskName' is installed and will start automatically at logon."
Write-Host "Logs: $env:LOCALAPPDATA\LM360Driver\logs\pc_display.log"
