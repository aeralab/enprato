$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$listeners = @(Get-NetTCPConnection -LocalPort 18787 -State Listen -ErrorAction SilentlyContinue)
foreach ($conn in $listeners) {
    $procId = $conn.OwningProcess
    if ($procId -and $procId -ne $PID) {
        Write-Host "Stopping backend pid $procId"
        Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
    }
}

Start-Sleep -Seconds 1
Write-Host "Starting backend..."
Start-Process powershell -ArgumentList @(
    '-NoExit',
    '-File',
    (Join-Path $PSScriptRoot 'start-backend.ps1')
)
