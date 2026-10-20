@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1

set "PYCMD="
where py >nul 2>nul && set "PYCMD=py -3"
if not defined PYCMD where python >nul 2>nul && set "PYCMD=python"
if not defined PYCMD (
  echo Python 3.10 ou plus recent est introuvable. Installez-le depuis python.org puis relancez.
  pause
  exit /b 1
)

if not defined GEMINI_API_KEY (
  for /f "usebackq delims=" %%i in (`powershell -NoProfile -Command "$s = Read-Host 'Cle API Gemini (laisser vide pour le mode manuel)' -AsSecureString; [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($s))"`) do set "GEMINI_API_KEY=%%i"
)

%PYCMD% server.py
pause
