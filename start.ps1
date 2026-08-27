$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$ffmpegDirs = @(
  $env:FFMPEG_PATH,
  "F:\softinstall\ffmpeg\bin",
  "C:\ffmpeg\ffmpeg\bin"
)
foreach ($dir in $ffmpegDirs) {
  if ($dir -and (Test-Path $dir)) {
    $env:Path = "$dir;$env:Path"
    break
  }
}

$ytdlpDirs = @(
  "C:\Users\Administrator\.agent-reach-venv\Scripts"
)
foreach ($dir in $ytdlpDirs) {
  if (Test-Path (Join-Path $dir "yt-dlp.exe")) {
    $env:Path = "$dir;$env:Path"
    break
  }
}

if (-not $env:WHISPER_MODEL) { $env:WHISPER_MODEL = "small.en" }
if (-not $env:WHISPER_DEVICE) { $env:WHISPER_DEVICE = "cuda" }

$frontend = Join-Path $PSScriptRoot "frontend"
if (-not (Test-Path (Join-Path $frontend "node_modules"))) {
  Push-Location $frontend
  npm.cmd install
  Pop-Location
}

Write-Host "Enprato backend https://0.0.0.0:18787 (手机麦)  frontend :5173"
Start-Process powershell -ArgumentList @(
  "-NoExit",
  "-File",
  (Join-Path $PSScriptRoot "start-backend.ps1")
)

Push-Location $frontend
npm.cmd run dev
Pop-Location
