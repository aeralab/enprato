$listen = Get-NetTCPConnection -LocalPort 18787 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($listen) {
    $procId = $listen.OwningProcess
    if ($procId -gt 0) {
        Stop-Process -Id $procId -Force
        Write-Output "killed $procId"
    } else {
        Write-Output "no valid process on 18787"
    }
} else {
    Write-Output "no process on 18787"
}
