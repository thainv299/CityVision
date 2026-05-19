import os
from typing import Any, Dict, Optional
from fastapi import Request, HTTPException, status
from frontend.database import get_user_by_id

def get_current_user_from_request(request: Request) -> Optional[Dict[str, Any]]:
    try:
        user_id = request.session.get("user_id")
        if user_id is None:
            return None
        user = get_user_by_id(int(user_id))
        if user is None or not user["is_active"]:
            request.session.clear()
            return None
        return user
    except AssertionError:
        return None

def require_login(request: Request):
    user = get_current_user_from_request(request)
    if user is None:
        if request.url.path.startswith("/api/"):
            raise HTTPException(status_code=401, detail="Bạn cần đăng nhập để tiếp tục.")
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})
    return user

def require_admin(request: Request):
    user = require_login(request)
    if user.get("role") != "admin":
        if request.url.path.startswith("/api/"):
            raise HTTPException(status_code=403, detail="Bạn không có quyền truy cập chức năng này.")
        raise HTTPException(status_code=403, detail="Tài khoản hiện tại không được phép mở trang này.")
    return user
