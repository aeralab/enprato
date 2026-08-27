$ErrorActionPreference = 'Continue'
Write-Output '=== default device ==='
$root = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render'
Get-ChildItem $root | ForEach-Object {
  $state = (Get-ItemProperty $_.PSPath).DeviceState
  if ($state -ne 1) { return }
  $p = Get-ItemProperty (Join-Path $_.PSPath 'Properties')
  $name = ''
  foreach ($n in $p.PSObject.Properties.Name) {
    if ($n -like '*a45c254e-df1c-4efd-8020-67d146a850e0},2') { $name = [string]$p.$n }
  }
  Write-Output ("ACTIVE: $name")
}

Write-Output '=== speaker-play then list ffplay ==='
$body = '{"start":5.0,"end":10.0,"volume":1.0}'
try {
  $r = Invoke-WebRequest -Uri 'http://127.0.0.1:18787/api/session/67494c1887c4/speaker-play' -Method POST -ContentType 'application/json' -Body $body -UseBasicParsing
  Write-Output ("api=" + $r.StatusCode + " " + $r.Content)
} catch {
  Write-Output ("api_err=" + $_.Exception.Message)
}
Start-Sleep -Seconds 1
Get-Process ffplay -ErrorAction SilentlyContinue | Select-Object Id,ProcessName,StartTime | Format-Table | Out-String
Start-Sleep -Seconds 2

Write-Output '=== winsound beep ==='
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.Speak('audio test')
Write-Output 'speech_done'
