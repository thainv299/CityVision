#!/bin/bash

echo "[$(date)] Bắt đầu cập nhật mã nguồn..." >> /tmp/cityvision_deploy.log

cd "$(dirname "$0")"
git fetch --all
git reset --hard origin/main
git pull origin main

if [ -d "venv" ]; then
    source venv/bin/activate
    pip3 install -r deploy/requirements_jetson.txt
fi

# Khởi động lại service bằng quyền hệ thống (chạy trong nền qua nohup để không bị nghẽn API)
nohup sudo systemctl restart cityvision.service > /dev/null 2>&1 &

echo "[$(date)] Cập nhật hoàn tất, hệ thống đang khởi động lại..." >> /tmp/cityvision_deploy.log
