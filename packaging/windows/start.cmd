@echo off
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
set CODE=%ERRORLEVEL%
if not "%CODE%"=="0" pause
exit /b %CODE%
