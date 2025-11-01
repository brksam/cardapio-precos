$ErrorActionPreference = "Stop"
$projectDir = "C:\Users\Administrator\Desktop\juninho"
Set-Location $projectDir

$pythonPath = Join-Path $projectDir ".venv\Scripts\python.exe"
if (-not (Test-Path $pythonPath)) { throw "Python do venv não encontrado: $pythonPath" }

& $pythonPath -m pip install --upgrade pip
& $pythonPath -m pip install playwright
& $pythonPath -m playwright install chromium
Write-Host "Playwright instalado."