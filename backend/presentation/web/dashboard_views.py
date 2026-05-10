from fastapi import APIRouter, Request, Depends, status
from fastapi.responses import RedirectResponse

from presentation.container import container, templates
from presentation.middlewares.auth import get_current_user, login_required
from fastapi.responses import HTMLResponse

dashboard_router = APIRouter()


@dashboard_router.get("/", name="dashboard.index")
async def index(request: Request):
    if get_current_user(request) is not None:
        return RedirectResponse(url=request.url_for("dashboard.dashboard_page"), status_code=status.HTTP_303_SEE_OTHER)
    return RedirectResponse(url=request.url_for("auth.login_page"), status_code=status.HTTP_303_SEE_OTHER)


@dashboard_router.get("/dashboard", name="dashboard.dashboard_page")
async def dashboard_page(request: Request, period: str = "all", user=Depends(login_required)):
    if isinstance(user, RedirectResponse):
        return user
    
    import sqlite3
    from backend.core.config import DATABASE_PATH
    
    latest_violations = []
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        rows = cursor.execute(
            "SELECT type, license_plate, camera_id, time FROM violations ORDER BY time DESC LIMIT 5"
        ).fetchall()
        latest_violations = [dict(row) for row in rows]
        conn.close()
    except Exception as e:
        print("Lỗi lấy thông báo chuông:", e)
    
    stats = container.dashboard_use_cases.get_dashboard_stats(period)
    
    stats["latest_violations"] = latest_violations
    stats["unread_notifications"] = len(latest_violations)
        
    return container.render_template(
        request,
        "dashboard.html",
        {
            "page": "dashboard",
            "stats": stats,
        }
    )



@dashboard_router.get("/api/dashboard")
async def api_dashboard(period: str = "all", user=Depends(login_required)):
    return {"ok": True, "stats": container.dashboard_use_cases.get_dashboard_stats(period)}

@dashboard_router.get("/settings", response_class=HTMLResponse, name="dashboard.settings_page")
async def settings_page(request: Request, user=Depends(login_required)):
    if isinstance(user, RedirectResponse):
        return user
    return container.render_template(
        request, 
        "settings.html", 
        {
            "page": "settings", 
            "settings": container.dashboard_use_cases.get_settings()
        }
    )

@dashboard_router.post("/api/settings")
async def api_update_settings(request: Request, user=Depends(login_required)):
    try:
        payload = await request.json()
        container.dashboard_use_cases.update_settings(payload)
        return {"ok": True, "message": "Cấu hình hệ thống đã được cập nhật."}
    except Exception as e:
        return {"ok": False, "message": f"Lỗi: {str(e)}"}

@dashboard_router.get("/api/search")
async def api_global_search(q: str = "", user=Depends(login_required)):
    results = container.dashboard_use_cases.search(q)
    return {"ok": True, "results": results}
