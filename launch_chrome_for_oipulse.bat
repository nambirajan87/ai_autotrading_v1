@echo off
title OI Pulse Chrome Remote Debugging Launcher
echo ====================================================================
echo Starting Google Chrome with Remote Debugging for OI Pulse...
echo ====================================================================
echo.
echo Port: 9222
echo Profile: %USERPROFILE%\ChromeOIProfile
echo Target: https://oipulse.com/signin/
echo.
start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="%USERPROFILE%\ChromeOIProfile" "https://oipulse.com/signin/"
echo Chrome launched successfully!
echo.
echo Please log in manually in the opened Chrome window.
echo Once logged in, open the Trending OI page and let the scheduler fetch Row 1.
echo.
pause
