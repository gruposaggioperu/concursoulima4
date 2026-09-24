@echo off
cd /d "%~dp0"
echo Deteniendo supervisor y servidor...

if exist ".servidor.lock" (
  for /f "usebackq delims=" %%p in (".servidor.lock") do (
    taskkill /F /PID %%p >nul 2>&1
  )
  del /f /q ".servidor.lock" >nul 2>&1
)

for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8080.*LISTENING"') do taskkill /F /PID %%a >nul 2>&1

echo Listo. Puerto 8080 liberado.
pause
