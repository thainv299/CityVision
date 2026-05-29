from fastapi import APIRouter, Request, Depends, status
from fastapi.responses import RedirectResponse
from fastapi.concurrency import run_in_threadpool
from core.config import PROJECT_ROOT
from presentation.container import container, templates
from presentation.middlewares.auth import get_current_user, login_required
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi import HTTPException
import os
import shutil
import time
import threading
from datetime import datetime
dashboard_router = APIRouter()


@dashboard_router.get("/", name="dashboard.index")
def index(request: Request):
    if get_current_user(request) is not None:
        return RedirectResponse(url=request.url_for("dashboard.dashboard_page"), status_code=status.HTTP_303_SEE_OTHER)
    return RedirectResponse(url=request.url_for("auth.login_page"), status_code=status.HTTP_303_SEE_OTHER)


@dashboard_router.get("/dashboard", name="dashboard.dashboard_page")
def dashboard_page(request: Request, period: str = "all", camera_id: str = "all", user=Depends(login_required)):
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
def api_dashboard(period: str = "all", user=Depends(login_required)):
    # Operator: lọc theo camera được cấp quyền
    camera_ids = None
    if not user.is_admin():
        accessible_cameras = container.camera_use_cases.list_cameras_for_user(user)
        camera_ids = [c.id for c in accessible_cameras]
    return {"ok": True, "stats": container.dashboard_use_cases.get_dashboard_stats(period, camera_ids=camera_ids)}

@dashboard_router.get("/settings", response_class=HTMLResponse, name="dashboard.settings_page")
def settings_page(request: Request, user=Depends(login_required)):
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
def api_global_search(q: str = "", user=Depends(login_required)):
    results = container.dashboard_use_cases.search(q)
    return {"ok": True, "results": results}
@dashboard_router.get("/api/notifications")
def api_notifications(limit: int = 10, user=Depends(login_required)):
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
        
        success = await run_in_threadpool(container.dashboard_use_cases.mark_notification_read, notif_type, int(record_id))
        return {"ok": success}
    except Exception as e:
        return {"ok": False, "message": str(e)}

# --- BACKUP & RESTORE API ---

def get_backup_dir():
    # Sử dụng biến môi trường BACKUP_DIR hoặc mặc định lưu trong thư mục backups ở gốc dự án
    backup_path = os.getenv("BACKUP_DIR", str(PROJECT_ROOT / "backups"))
    os.makedirs(backup_path, exist_ok=True)
    return backup_path

@dashboard_router.get("/api/system/backups")
def api_list_backups(user=Depends(login_required)):
    if not user.is_admin():
        raise HTTPException(status_code=403, detail="Forbidden")
    
    backup_dir = get_backup_dir()
    backups = []
    
    try:
        for item in os.listdir(backup_dir):
            item_path = os.path.join(backup_dir, item)
            if os.path.isdir(item_path):
                # Calculate total size
                total_size = 0
                for dirpath, _, filenames in os.walk(item_path):
                    for f in filenames:
                        fp = os.path.join(dirpath, f)
                        if not os.path.islink(fp):
                            total_size += os.path.getsize(fp)
                
                try:
                    dt = datetime.strptime(item, "backup_%Y%m%d_%H%M%S")
                    timestamp = dt.isoformat()
                except ValueError:
                    timestamp = datetime.fromtimestamp(os.path.getctime(item_path)).isoformat()
                
                backups.append({
                    "id": item,
                    "timestamp": timestamp,
                    "size_mb": round(total_size / (1024 * 1024), 2)
                })
    except Exception as e:
        print(f"Lỗi khi đọc danh sách backup: {e}")
        
    backups.sort(key=lambda x: x["timestamp"], reverse=True)
    return {"ok": True, "backups": backups}

@dashboard_router.post("/api/system/backups")
def api_create_backup(user=Depends(login_required)):
    if not user.is_admin():
        raise HTTPException(status_code=403, detail="Forbidden")
        
    backup_dir = get_backup_dir()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    folder_name = f"backup_{timestamp}"
    target_dir = os.path.join(backup_dir, folder_name)
    
    try:
        os.makedirs(target_dir, exist_ok=True)
        
        # 1. Copy database
        db_path = PROJECT_ROOT / "urbanm_ai.db"
        if db_path.exists():
            shutil.copy2(db_path, os.path.join(target_dir, "urbanm_ai.db"))
            
        # 2. Copy logs folder
        logs_path = PROJECT_ROOT / "logs"
        if logs_path.exists():
            target_logs = os.path.join(target_dir, "logs")
            shutil.copytree(logs_path, target_logs, dirs_exist_ok=True)
            
        return {"ok": True, "message": "Tạo bản sao lưu thành công", "backup_id": folder_name}
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "message": str(e)})

@dashboard_router.post("/api/system/backups/{backup_id}/restore")
def api_restore_backup(backup_id: str, user=Depends(login_required)):
    if not user.is_admin():
        raise HTTPException(status_code=403, detail="Forbidden")
        
    backup_dir = get_backup_dir()
    source_dir = os.path.join(backup_dir, backup_id)
    
    if not os.path.isdir(source_dir):
        return JSONResponse(status_code=404, content={"ok": False, "message": "Bản sao lưu không tồn tại"})
        
    try:
        # 1. Restore database
        source_db = os.path.join(source_dir, "urbanm_ai.db")
        if os.path.exists(source_db):
            shutil.copy2(source_db, PROJECT_ROOT / "urbanm_ai.db")
            
        # 2. Restore logs
        source_logs = os.path.join(source_dir, "logs")
        if os.path.exists(source_logs):
            shutil.copytree(source_logs, PROJECT_ROOT / "logs", dirs_exist_ok=True)
            
        def delayed_restart():
            print("[System] Hệ thống đang tự động khởi động lại sau lệnh khôi phục...")
            time.sleep(1.5)
            import sys
            os.execv(sys.executable, [sys.executable] + sys.argv)
            
        threading.Thread(target=delayed_restart, daemon=True).start()
            
        return {"ok": True, "message": "Khôi phục thành công. Hệ thống đang tự động khởi động lại..."}
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "message": str(e)})

@dashboard_router.delete("/api/system/backups/{backup_id}")
def api_delete_backup(backup_id: str, user=Depends(login_required)):
    if not user.is_admin():
        raise HTTPException(status_code=403, detail="Forbidden")
        
    backup_dir = get_backup_dir()
    target_dir = os.path.join(backup_dir, backup_id)
    
    if not os.path.isdir(target_dir):
        return JSONResponse(status_code=404, content={"ok": False, "message": "Bản sao lưu không tồn tại"})
        
    try:
        shutil.rmtree(target_dir)
        return {"ok": True, "message": "Đã xoá bản sao lưu"}
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "message": str(e)})