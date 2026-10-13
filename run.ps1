# Start Job Agent (backend + frontend). Ctrl+C in each window to stop.
$root = $PSScriptRoot
Write-Host "Starting backend on http://127.0.0.1:8000 ..." -ForegroundColor Cyan
Start-Process -FilePath "$root\.venv\Scripts\python.exe" `
  -ArgumentList "-m","uvicorn","app.main:app","--host","127.0.0.1","--port","8000" `
  -WorkingDirectory "$root\backend"
Start-Sleep -Seconds 3
Write-Host "Starting frontend on http://localhost:5173 ..." -ForegroundColor Cyan
Start-Process -FilePath "npm.cmd" -ArgumentList "run","dev" -WorkingDirectory "$root\frontend"
Start-Sleep -Seconds 4
Start-Process "http://localhost:5173"
Write-Host "Open http://localhost:5173" -ForegroundColor Green
