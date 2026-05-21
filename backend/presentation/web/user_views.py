from fastapi import APIRouter, Request, Depends, status
from fastapi.responses import JSONResponse, RedirectResponse
from typing import Any, Dict, List

from core.errors import AppError
from presentation.container import container, templates
from presentation.middlewares.auth import admin_required, get_current_user

user_router = APIRouter()


@user_router.get("/users", name="users.users_page")
def users_page(request: Request, user=Depends(admin_required)):
    if isinstance(user, RedirectResponse):
        return user
    # Truyền danh sách tất cả camera xuống template để quản lý quyền
    cameras = container.camera_use_cases.list_cameras()
    return container.render_template(request, "users.html", {
        "page": "users",
        "all_cameras": [c.to_dict() for c in cameras]
    })


@user_router.get("/api/users")
def api_list_users(user=Depends(admin_required)):
    users = container.user_use_cases.list_users()
    return {"ok": True, "users": [u.to_dict() for u in users]}


@user_router.post("/api/users")
def api_create_user(payload: Dict[str, Any], user=Depends(admin_required)):
    try:
        created = container.user_use_cases.create_user(payload)
        return JSONResponse(status_code=status.HTTP_201_CREATED, content={"ok": True, "user": created.to_dict()})
    except AppError as exc:
        return JSONResponse(status_code=exc.status_code, content={"ok": False, "error": exc.message})
    except Exception as e:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(e)})


@user_router.put("/api/users/{user_id}")
def api_update_user(user_id: int, payload: Dict[str, Any], user=Depends(admin_required)):
    try:
        updated = container.user_use_cases.update_user(user_id, payload, current_role=user.role if user else "")
        return {"ok": True, "user": updated.to_dict()}
    except AppError as exc:
        return JSONResponse(status_code=exc.status_code, content={"ok": False, "error": exc.message})
    except Exception as e:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(e)})


@user_router.delete("/api/users/{user_id}")
def api_delete_user(user_id: int, user=Depends(admin_required)):
    try:
        container.user_use_cases.delete_user(user_id, user.id if user else -1)
        return {"ok": True}
    except AppError as exc:
        return JSONResponse(status_code=exc.status_code, content={"ok": False, "error": exc.message})
    except Exception as e:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(e)})


@user_router.get("/api/users/{user_id}/camera-access")
def api_get_camera_access(user_id: int, user=Depends(admin_required)):
    """Lấy danh sách ID camera mà user được truy cập"""
    try:
        target = container.user_use_cases.get_user(user_id)
        return {"ok": True, "camera_ids": target.camera_access_ids or []}
    except AppError as exc:
        return JSONResponse(status_code=exc.status_code, content={"ok": False, "error": exc.message})


@user_router.put("/api/users/{user_id}/camera-access")
def api_update_camera_access(user_id: int, payload: Dict[str, Any], user=Depends(admin_required)):
    """Cập nhật danh sách quyền truy cập camera cho user (chỉ dành cho operator)"""
    try:
        camera_ids = payload.get("camera_ids", [])
        container.user_use_cases.update_camera_access(user_id, camera_ids)
        return {"ok": True, "message": "Đã cập nhật quyền truy cập camera."}
    except AppError as exc:
        return JSONResponse(status_code=exc.status_code, content={"ok": False, "error": exc.message})
    except Exception as e:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(e)})

