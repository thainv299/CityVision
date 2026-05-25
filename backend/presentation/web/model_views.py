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
                
        return {"ok": True, "message": f"Đã tải lên model {file.filename} thành công!"}
    except Exception as e:
        import traceback
        err_msg = traceback.format_exc()
        return JSONResponse(status_code=500, content={"ok": False, "error": err_msg})

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
