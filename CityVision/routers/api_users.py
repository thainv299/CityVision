import sqlite3
from fastapi import APIRouter, Request, Depends
from fastapi.responses import JSONResponse

from core.utils import json_error, validate_user_payload
from core.security import require_login, require_admin
from frontend.database import (
    get_user_record_by_id,
    create_user,
    update_user,
    delete_user,
    list_users,
    count_admin_users,
    count_active_users,
    get_dashboard_stats
)

router = APIRouter(prefix="/api")

@router.get("/session")
def api_session(request: Request, user=Depends(require_login)):
    return {"ok": True, "user": user}

@router.get("/dashboard")
def api_dashboard(user=Depends(require_login)):
    return {"ok": True, "stats": get_dashboard_stats()}

@router.get("/users")
def api_list_users(user=Depends(require_admin)):
    return {"ok": True, "users": list_users()}

@router.post("/users")
def api_create_user(payload: dict, user=Depends(require_admin)):
    try:
        user_payload = validate_user_payload(payload, creating=True)
        created = create_user(user_payload)
        return JSONResponse({"ok": True, "user": created}, status_code=201)
    except ValueError as exc:
        return json_error(str(exc), 400)
    except sqlite3.IntegrityError:
        return json_error("Tên đăng nhập đã tồn tại.", 409)

@router.put("/users/{user_id}")
def api_update_user(user_id: int, payload: dict, user=Depends(require_admin)):
    existing = get_user_record_by_id(user_id)
    if existing is None:
        return json_error("Không tìm thấy người dùng.", 404)
    try:
        user_payload = validate_user_payload(payload, creating=False)
        if "password_hash" not in user_payload and not payload.get("password"):
            user_payload.pop("password_hash", None)
        if existing["role"] == "admin" and user_payload["role"] != "admin" and count_admin_users() <= 1:
            return json_error("Cần giữ lại ít nhất một tài khoản admin.", 400)
        if bool(existing["is_active"]) and not user_payload.get("is_active", True) and count_active_users() <= 1:
            return json_error("Cần giữ lại ít nhất một tài khoản đang hoạt động.", 400)

        updated = update_user(user_id, user_payload)
        if updated is None:
            return json_error("Không tìm thấy người dùng.", 404)
        return {"ok": True, "user": updated}
    except ValueError as exc:
        return json_error(str(exc), 400)
    except sqlite3.IntegrityError:
        return json_error("Tên đăng nhập đã tồn tại.", 409)

@router.delete("/users/{user_id}")
def api_delete_user(user_id: int, request: Request, user=Depends(require_admin)):
    target = get_user_record_by_id(user_id)
    if target is None:
        return json_error("Không tìm thấy người dùng.", 404)
    if user["id"] == user_id:
        return json_error("Không thể tự xóa chính mình.", 400)
    if target["role"] == "admin" and count_admin_users() <= 1:
        return json_error("Cần giữ lại ít nhất một tài khoản admin.", 400)

    deleted = delete_user(user_id)
    if not deleted:
        return json_error("Không thể xóa người dùng.", 400)
    return {"ok": True}
