@echo off
title Apparel POS System Launcher

cd /d "%~dp0"

echo Starting the Apparel POS System...

:: Start Flask app minimized
start "" /min python app.py

:: Wait until server is ready (better than fixed timeout)
:waitloop
timeout /t 1 > nul
curl -s http://127.0.0.1:5000 > nul
if errorlevel 1 goto waitloop

:: Open browser automatically
start "" "http://127.0.0.1:5000/pos"

exit