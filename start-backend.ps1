$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$env:Path = "F:\softinstall\ffmpeg\bin;$env:Path"
$env:HF_HOME = Join-Path $PSScriptRoot "backend\data\hf_home"
$env:HUGGINGFACE_HUB_CACHE = Join-Path $PSScriptRoot "backend\data\models\hub"
if (-not $env:WHISPER_MODEL) { $env:WHISPER_MODEL = 'small.en' }
if (-not $env:WHISPER_DEVICE) { $env:WHISPER_DEVICE = 'cuda' }
$py = if (Test-Path 'F:\softinstall\python.exe') { 'F:\softinstall\python.exe' } else { 'python' }

$pair = & $py -c "from backend.app.certs import ensure_lan_certs; c,k=ensure_lan_certs(); print(c); print(k)"
$lines = @($pair | ForEach-Object { "$_".Trim() } | Where-Object { $_ })
if ($lines.Count -lt 2) { throw "cert generation failed: $pair" }
$cert = $lines[0]
$key = $lines[1]
Write-Host "SSL cert: $cert"
Write-Host "Backend https://0.0.0.0:18787 (phone mic needs https)"
& $py -m uvicorn backend.app.main:app --host 0.0.0.0 --port 18787 --ssl-certfile $cert --ssl-keyfile $key --reload --reload-dir backend
