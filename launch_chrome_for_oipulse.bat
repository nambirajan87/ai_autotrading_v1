@echo off
title OI Pulse Chrome Remote Debugging Launcher
echo ====================================================================
echo Starting Google Chrome with Remote Debugging for OI Pulse...
echo ====================================================================
echo.
echo Port: 9222
echo Profile: %USERPROFILE%\ChromeOIProfile
echo Target: https://oipulse.com/app/options-analysis/trending-oi
echo.
start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="%USERPROFILE%\ChromeOIProfile" "https://oipulse.com/app/options-analysis/trending-oi"
echo Chrome launched successfully!
echo.
echo Please log in manually in the opened Chrome window.
echo Once logged in, run: python scripts/extract_oipulse_trending_oi.py
echo.
pause
