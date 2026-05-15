from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from presentation.middlewares.auth import login_required
from database.sqlite_db import get_unread_notifications, mark_notification_as_read

notification_router = APIRouter()

@notification_router.get("/api/notifications/unread")
async def api_get_unread_notifications(
    request: Request,
    user=Depends(login_required),
    limit: int = 10
):
    """Lấy danh sách thông báo chưa đọc"""
    result = get_unread_notifications(limit=limit)
    return {
        "ok": True,
        "unread_count": result["unread_count"],
        "notifications": result["items"]
    }

@notification_router.post("/api/notifications/{notif_type}/{record_id}/read")
async def api_mark_notification_read(
    notif_type: str,
    record_id: int,
    user=Depends(login_required)
):
    """Đánh dấu thông báo là đã đọc"""
    if notif_type not in ["violation", "congestion"]:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Loại thông báo không hợp lệ"})
        
    success = mark_notification_as_read(notif_type, record_id)
    if success:
        return {"ok": True}
    return JSONResponse(status_code=400, content={"ok": False, "error": "Không thể đánh dấu thông báo này"})