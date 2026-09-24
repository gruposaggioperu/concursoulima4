@echo off
cd /d "%~dp0"
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "LINK=%STARTUP%\Control Impuestos SUNAT.lnk"
set "TARGET=%~dp0iniciar_oculto.vbs"

powershell -NoProfile -Command ^
  "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%LINK%');" ^
  "$s.TargetPath='%TARGET%';" ^
  "$s.WorkingDirectory='%~dp0';" ^
  "$s.WindowStyle=7;" ^
  "$s.Description='Control de Impuestos - servidor local SUNAT';" ^
  "$s.Save()"

echo.
echo Autoinicio instalado.
echo Al encender Windows se iniciara el servidor en segundo plano.
echo URL: http://127.0.0.1:8080/
echo.
echo Para quitar: elimina el acceso directo en:
echo   %STARTUP%
echo   "Control Impuestos SUNAT"
echo.
pause
