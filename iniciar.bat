@echo off
cd /d "%~dp0"
title Control de Impuestos - Servidor
echo ========================================
echo   Control de Impuestos - Modo automatico
echo ========================================
echo.
echo - Si el servidor ya corre, abre el navegador.
echo - Si se cae, se reinicia solo.
echo - Ctrl+C para detener.
echo.
python servidor_auto.py
pause
