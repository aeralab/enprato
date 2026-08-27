# 快速试音：确认本机默认设备是否真的能出声
$ErrorActionPreference = 'Continue'
Write-Output 'Playing Alarm01.wav on default device...'
(New-Object Media.SoundPlayer 'C:\Windows\Media\Alarm01.wav').PlaySync()
Write-Output 'If you heard nothing, Digital Output has no working speakers attached.'
Write-Output 'Open sound settings...'
Start-Process 'ms-settings:sound'
