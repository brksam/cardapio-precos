$ErrorActionPreference = "Stop"

# Caminhos (ajuste se necessário)
$projectDir = "C:\Users\Administrator\Desktop\juninho"
$credPath   = "C:\Users\Administrator\Desktop\serviceAccountKey.json"
$pythonPath = Join-Path $projectDir ".venv\Scripts\python.exe"   # Python do venv

# Vai para a pasta do projeto
Set-Location $projectDir

# Verificações rápidas
if (-not (Test-Path $pythonPath)) { throw "Python do venv não encontrado: $pythonPath" }
if (-not (Test-Path $credPath))   { throw "Credencial não encontrada: $credPath" }

# Variáveis de ambiente do scraping
$env:GOOGLE_APPLICATION_CREDENTIALS = $credPath
$env:HEADLESS  = "1"  # 1 = sem abrir navegador (headless)
$env:MAX_ITEMS = "0"  # 0 = sem limite
$env:DEBUG_LOG = "0"  # 1 = logs de debug no console
$env:PYTHONIOENCODING = "utf-8"

# Pasta de logs
$logDir = Join-Path $projectDir "logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$stamp   = Get-Date -Format "yyyyMMdd_HHmmss"
$logFile = Join-Path $logDir "scrape_$stamp.txt"

# Cabeçalho (UTF-8)
"[{0}] Iniciando scraping..." -f (Get-Date -Format o) | Out-File -FilePath $logFile -Encoding UTF8

# Execução via CMD com codepage UTF-8 e redirecionamento para o log (append)
# Observação: usamos CMD para evitar que o PowerShell mude o encoding do redirecionamento.
$cmd = "chcp 65001 >nul & ""$pythonPath"" "".\lg1.py"" >> ""$logFile"" 2>&1"
& cmd /c $cmd

# Rodapé (UTF-8)
$exitCode = $LASTEXITCODE
"[{0}] Finalizado. ExitCode={1}" -f (Get-Date -Format o), $exitCode | Out-File -FilePath $logFile -Append -Encoding UTF8

if ($exitCode -ne 0) {
    throw "Scraping terminou com ExitCode $exitCode. Veja o log: $logFile"
}