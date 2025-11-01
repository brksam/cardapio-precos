$ErrorActionPreference = "Stop"
$projectDir = "C:\Users\Administrator\Desktop\juninho"
$credPath   = "C:\Users\Administrator\Desktop\serviceAccountKey.json"
Set-Location $projectDir

$env:GOOGLE_APPLICATION_CREDENTIALS = $credPath
& .\.venv\Scripts\Activate.ps1
streamlit run ".\dashboard.py" --server.address 0.0.0.0 --server.port 8501