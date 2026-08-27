$ErrorActionPreference = 'Continue'
Write-Output '=== USB Audio devices detail ==='
Get-PnpDevice | Where-Object { $_.FriendlyName -match 'USB Audio' } | Format-List Status,Class,FriendlyName,InstanceId,Problem | Out-String -Width 200

Write-Output '=== try enable USB Audio MEDIA ==='
$devs = Get-PnpDevice | Where-Object { $_.FriendlyName -eq 'USB Audio' -or $_.FriendlyName -match '扬声器 \(USB' }
foreach ($d in $devs) {
  Write-Output ("enabling " + $d.FriendlyName + " " + $d.InstanceId)
  try {
    Enable-PnpDevice -InstanceId $d.InstanceId -Confirm:$false -ErrorAction Stop
    Write-Output 'enabled'
  } catch {
    Write-Output ("enable_err: " + $_.Exception.Message)
  }
}

Start-Sleep -Seconds 2
Write-Output '=== active render after enable ==='
$root = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render'
Get-ChildItem $root | ForEach-Object {
  $state = (Get-ItemProperty $_.PSPath).DeviceState
  $p = Get-ItemProperty (Join-Path $_.PSPath 'Properties')
  $name = ''
  foreach ($n in $p.PSObject.Properties.Name) {
    if ($n -like '*a45c254e-df1c-4efd-8020-67d146a850e0},2') { $name = [string]$p.$n }
  }
  if ($state -eq 1 -or $name -match 'USB|扬声器|Speaker|Head') {
    Write-Output ("State=$state Name=$name")
  }
}
