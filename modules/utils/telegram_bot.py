from __future__ import annotations
import os
import requests

def send_telegram_image(
    img_path: str,
    caption: str,
    bot_token: str | None = None,
    chat_id: str | None = None,
    timeout_seconds: int = 10,
) -> bool:
    """Gửi ảnh qua Telegram"""
    try:
        from backend.database.sqlite_db import get_system_setting
        if get_system_setting("telegram_notifications", "true") == "false":
            print("[Telegram Bot] Gửi ảnh bị bỏ qua do cấu hình hệ thống đã tắt thông báo Telegram.")
            return False
    except Exception as e:
        print(f"[Telegram Bot] Lỗi kiểm tra cấu hình hệ thống: {e}")

    bot_token = (bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")).strip()
    chat_id = (chat_id or os.getenv("TELEGRAM_CHAT_ID", "")).strip()

    if not bot_token or not chat_id:
        return False
        
    url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"

    try:
        with open(img_path, "rb") as f:
            resp = requests.post(
                url,
                data={"chat_id": chat_id, "caption": caption},
                files={"photo": f},
                timeout=timeout_seconds,
            )
        print("Telegram response (image):", resp.status_code)
        return resp.status_code == 200
    except Exception as e:
        print("Lỗi gửi ảnh Telegram:", e)
        return False


def send_telegram_video(
    video_path: str,
    caption: str,
    bot_token: str | None = None,
    chat_id: str | None = None,
    timeout_seconds: int = 10,
) -> bool:
    """Gửi video qua Telegram"""
    try:
        from backend.database.sqlite_db import get_system_setting
        if get_system_setting("telegram_notifications", "true") == "false":
            print("[Telegram Bot] Gửi video bị bỏ qua do cấu hình hệ thống đã tắt thông báo Telegram.")
            return False
    except Exception as e:
        print(f"[Telegram Bot] Lỗi kiểm tra cấu hình hệ thống: {e}")

    bot_token = (bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")).strip()
    chat_id = (chat_id or os.getenv("TELEGRAM_CHAT_ID", "")).strip()

    if not bot_token or not chat_id:
        return False

    if not os.path.exists(video_path):
        print(f"File video không tồn tại: {video_path}")
        return False

    url = f"https://api.telegram.org/bot{bot_token}/sendVideo"

    try:
        with open(video_path, "rb") as f:
            resp = requests.post(
                url,
                data={"chat_id": chat_id, "caption": caption},
                files={"video": f},
                timeout=timeout_seconds,
            )
        print("Telegram response (video):", resp.status_code)
        return resp.status_code == 200
    except Exception as e:
        print("Lỗi gửi video Telegram:", e)
        return False