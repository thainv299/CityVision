from fastapi import APIRouter, Request, Depends, UploadFile, File, status
from fastapi.responses import RedirectResponse, JSONResponse
from pathlib import Path
import os
import datetime
from database.sqlite_db import create_system_notification, connect
import threading
from presentation.container import container
from presentation.middlewares.auth import admin_required

model_router = APIRouter()

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
MODELS_DIR = PROJECT_ROOT / "models"

@model_router.get("/models", name="models.models_page")
def models_page(request: Request, user=Depends(admin_required)):
    if isinstance(user, RedirectResponse):
        return user
    return container.render_template(request, "models.html", {
        "page": "models"
    })

@model_router.get("/api/models")
def list_models(user=Depends(admin_required)):
    if isinstance(user, RedirectResponse):
        return JSONResponse(status_code=403, content={"ok": False, "error": "Unauthorized"})
    try:
        models_info = []
        if MODELS_DIR.exists():
            for f in MODELS_DIR.iterdir():
                if f.is_file():
                    stat = f.stat()
                    size_mb = stat.st_size / (1024 * 1024)
                    modified = datetime.datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
                    models_info.append({
                        "name": f.name,
                        "size_mb": round(size_mb, 2),
                        "modified": modified
                    })
        # Sort by modified desc
        models_info.sort(key=lambda x: x["modified"], reverse=True)
        return {"ok": True, "models": models_info}
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})

def _export_model_to_engine_task(file_path: Path):
    try:
        from ultralytics import YOLO
        create_system_notification(
            "Đang chuyển đổi mô hình", 
            f"Mô hình {file_path.name} đang được xuất sang TensorRT (.engine). Hệ thống đã tự động dừng các AI camera để ưu tiên tài nguyên. Vui lòng chờ 5-15 phút..."
        )
        # Bắt đầu xuất (ưu tiên fp16)
        model = YOLO(str(file_path))
        model.export(format='engine', workspace=4, half=True, device='0')
        
        create_system_notification(
            "Chuyển đổi hoàn tất", 
            f"Mô hình {file_path.name} đã xuất xong định dạng .engine thành công!"
        )
    except Exception as e:
        create_system_notification(
            "Lỗi chuyển đổi mô hình", 
            f"Đã có lỗi xảy ra khi xuất {file_path.name}: {str(e)}"
        )

@model_router.post("/api/models/upload")
async def upload_model(file: UploadFile = File(...), user=Depends(admin_required)):
    if isinstance(user, RedirectResponse):
        return JSONResponse(status_code=403, content={"ok": False, "error": "Unauthorized"})
    try:
        if not MODELS_DIR.exists():
            MODELS_DIR.mkdir(parents=True, exist_ok=True)
            
        file_path = MODELS_DIR / file.filename
        
        import shutil
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
                
        # Nếu là file .pt, tự động stop camera và export ra .engine
        if file_path.suffix.lower() == '.pt':
            # 1. Cập nhật tắt AI trong DB
            with connect() as conn:
                conn.execute("UPDATE camera SET bat_xu_ly_ai = 0")
                cameras = conn.execute("SELECT id FROM camera").fetchall()
            
            # 2. Dừng các process đang chạy
            for cam in cameras:
                container.job_use_cases.stop_camera_jobs(cam['id'])
                
            # 3. Khởi chạy tiến trình ngầm export
            threading.Thread(target=_export_model_to_engine_task, args=(file_path,), daemon=True).start()
            
            return {"ok": True, "message": f"Đã tải lên {file.filename}. Đang tự động chuyển đổi sang TensorRT ngầm..."}
            
        return {"ok": True, "message": f"Đã tải lên model {file.filename} thành công!"}
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})

@model_router.delete("/api/models/{filename}")
def delete_model(filename: str, user=Depends(admin_required)):
    if isinstance(user, RedirectResponse):
        return JSONResponse(status_code=403, content={"ok": False, "error": "Unauthorized"})
    try:
        file_path = MODELS_DIR / filename
        if file_path.exists() and file_path.is_file():
            file_path.unlink()
            return {"ok": True, "message": f"Đã xóa model {filename}"}
        else:
            return JSONResponse(status_code=404, content={"ok": False, "error": "Không tìm thấy file model"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})
