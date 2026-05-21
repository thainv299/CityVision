from fastapi import APIRouter, Request, Depends, status
from fastapi.responses import RedirectResponse
from core.config import PROJECT_ROOT
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
async def dashboard_page(request: Request, period: str = "all", camera_id: str = "all", user=Depends(login_required)):
    if isinstance(user, RedirectResponse):
        return user
    
    # Xác định danh sách camera mà user có quyền truy cập
    accessible_cameras = container.camera_use_cases.list_cameras_for_user(user)
    accessible_ids = [c.id for c in accessible_cameras]
    
    # Nếu user chọn lọc theo 1 camera cụ thể, chỉ lấy camera đó (và phải nằm trong danh sách được phép)
    filter_camera_ids = None
    if camera_id != "all":
        try:
            selected_id = int(camera_id)
            if selected_id in accessible_ids:
                filter_camera_ids = [selected_id]
            else:
                filter_camera_ids = []  # Không có quyền → dữ liệu rỗng
        except ValueError:
            pass
    
    # Operator: luôn lọc theo camera được cấp quyền
    if not user.is_admin():
        if filter_camera_ids is None:
            filter_camera_ids = accessible_ids if accessible_ids else []
        else:
            # Đảm bảo chỉ lọc trong phạm vi cho phép
            filter_camera_ids = [cid for cid in filter_camera_ids if cid in accessible_ids]
    
    return container.render_template(
        request,
        "dashboard.html",
        {
            "page": "dashboard",
            "stats": container.dashboard_use_cases.get_dashboard_stats(period, camera_ids=filter_camera_ids),
            "cameras": [c.to_dict() for c in accessible_cameras],
            "selected_camera_id": camera_id,
        }
    )



@dashboard_router.get("/api/dashboard")
async def api_dashboard(period: str = "all", user=Depends(login_required)):
    # Operator: lọc theo camera được cấp quyền
    camera_ids = None
    if not user.is_admin():
        accessible_cameras = container.camera_use_cases.list_cameras_for_user(user)
        camera_ids = [c.id for c in accessible_cameras]
    return {"ok": True, "stats": container.dashboard_use_cases.get_dashboard_stats(period, camera_ids=camera_ids)}

@dashboard_router.get("/settings", response_class=HTMLResponse, name="dashboard.settings_page")
async def settings_page(request: Request, user=Depends(login_required)):
    if isinstance(user, RedirectResponse):
        return user
    # Chỉ trả camera mà user có quyền truy cập
    cameras = container.camera_use_cases.list_cameras_for_user(user)
    
    # Quét danh sách các mô hình có sẵn tương tự như camera_views.py
    models_dir = PROJECT_ROOT / "models"
    available_models = []
    if models_dir.exists():
        for f in models_dir.iterdir():
            if f.is_file() and f.suffix.lower() in [".pt", ".engine"]:
                available_models.append(f.name)
    available_models.sort()

    return container.render_template(
        request, 
        "settings.html", 
        {
            "page": "settings", 
            "cameras": [c.to_dict() for c in cameras],
            "available_models": available_models
        }
    )

@dashboard_router.get("/api/search")
async def api_global_search(q: str = "", user=Depends(login_required)):
    results = container.dashboard_use_cases.search(q)
    return {"ok": True, "results": results}
@dashboard_router.get("/api/notifications")
async def api_notifications(limit: int = 10, user=Depends(login_required)):
    results = container.dashboard_use_cases.get_notifications(limit)
    return {"ok": True, "results": results}

@dashboard_router.post("/api/notifications/read")
async def api_mark_notification_read(request: Request, user=Depends(login_required)):
    try:
        payload = await request.json()
        notif_type = payload.get("type")
        record_id = payload.get("id")
        if not notif_type or not record_id:
            return {"ok": False, "message": "Missing type or id"}
        
        success = container.dashboard_use_cases.mark_notification_read(notif_type, int(record_id))
        return {"ok": success}
    except Exception as e:
        return {"ok": False, "message": str(e)}