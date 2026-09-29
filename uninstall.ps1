#Requires -RunAsAdministrator
<#
Removes the LM360CustomDisplay scheduled task and stops any running instance.
Leaves the PawnIO driver and pip packages installed since other tools may
depend on them - remove those manually if you want them fully gone.
#>

$ErrorActionPreference = "Stop"
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

Write-Host "== Stopping and removing scheduled task =="
Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

Write-Host "== Stopping any running pc_display.py process =="
Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'" |
    Where-Object { $_.CommandLine -match "pc_display\.py" } |
    ForEach-Object {
        Write-Host "Stopping PID $($_.ProcessId)"
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }

Write-Host ""
Write-Host "Done. Scheduled task '$TaskName' removed."
Write-Host "PawnIO driver and pip packages were left installed - remove manually if desired."
