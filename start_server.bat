@echo off
setlocal enabledelayedexpansion
title He Thong Giam Sat Giao Thong - CityVision
color 0A

:: 1. Doc file .env
echo [0/3] Dang doc cau hinh tu file .env...
for /f "tokens=1,2 delims==" %%a in (.env) do (
    if "%%a"=="CLOUDFLARE_TOKEN" set "MY_TOKEN=%%b"
)

if "%MY_TOKEN%"=="" (
    color 0C
    echo [LOI] Khong tim thay CLOUDFLARE_TOKEN trong file .env!
    pause
    exit
)

:: 2. Kich hoat moi truong Anaconda
echo [1/3] Dang kich hoat moi truong Conda (datn)...
call conda activate datn

:: 3. Khoi dong Backend
echo [2/3] Dang khoi dong AI Backend...
start "AI_Backend" cmd /k "conda activate datn && python run_system.py"

echo.
echo [3/3] Dang mo duong ham Cloudflare Tunnel...
echo Link: https://cityvision.id.vn
echo.

:: 4. Chay Tunnel
cloudflared.exe tunnel run --token %MY_TOKEN%

pause