@echo off
setlocal
if not defined PORT set PORT=8000

rem Stop whatever is listening on the app's port (the uvicorn server started by start.bat)
powershell -NoProfile -Command "$c = Get-NetTCPConnection -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue; if (-not $c) { Write-Host 'Nothing is listening on port %PORT%.'; exit 1 }; foreach ($pid_ in ($c.OwningProcess | Sort-Object -Unique)) { $p = Get-Process -Id $pid_ -ErrorAction SilentlyContinue; Write-Host ('Stopping ' + $p.ProcessName + ' (PID ' + $pid_ + ')'); Stop-Process -Id $pid_ -Force }"
if errorlevel 1 (
  endlocal
  exit /b 1
)
echo Stopped.
endlocal
