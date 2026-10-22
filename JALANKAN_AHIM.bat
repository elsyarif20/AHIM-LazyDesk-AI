@echo off
setlocal
cd /d "%~dp0"
title AHIM LazyDesk AI
if not exist ".venv\Scripts\python.exe" (
  echo Membuat virtual environment pertama kali...
  py -m venv .venv
  if errorlevel 1 (echo Gagal membuat virtual environment. Pastikan Python 3.11+ terpasang.& pause & exit /b 1)
)
".venv\Scripts\python.exe" -c "import flask, ollama, keyring, docx, pypdf, requests, openpyxl, reportlab, fitz" >nul 2>nul
if errorlevel 1 (
  echo Memasang dependensi yang dibutuhkan. Proses pertama mungkin memerlukan waktu...
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 (echo Instalasi dependensi gagal. Periksa internet dan versi Python.& pause & exit /b 1)
)
start "" http://127.0.0.1:5000
".venv\Scripts\python.exe" app.py
pause
