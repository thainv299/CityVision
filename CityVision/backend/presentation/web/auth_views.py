from typing import Optional
from fastapi import APIRouter, Request, Form, Depends, status
from fastapi.responses import RedirectResponse, JSONResponse
from werkzeug.security import check_password_hash

from presentation.container import container, templates
from presentation.middlewares.auth import get_current_user

auth_router = APIRouter()


@auth_router.get("/login", name="auth.login_page")
@auth_router.post("/login")
async def login_page(
    request: Request,
    username: Optional[str] = Form(None),
    password: Optional[str] = Form(None)
):
    if get_current_user(request) is not None:
        return RedirectResponse(url=request.url_for("dashboard.dashboard_page"), status_code=status.HTTP_303_SEE_OTHER)

    error = None
    if request.method == "POST":
        username = (username or "").strip()
        
        user_record = container.user_use_cases.user_repo.get_by_username(username)
        if user_record is None or not check_password_hash(user_record.password_hash, password or ""):
            error = "Tên đăng nhập hoặc mật khẩu không đúng."
        elif not user_record.is_active:
            error = "Tài khoản này đang bị khóa."
        else:
            request.session.clear()
            request.session["user_id"] = user_record.id
            return RedirectResponse(url=request.url_for("dashboard.dashboard_page"), status_code=status.HTTP_303_SEE_OTHER)

    return container.render_template(request, "login.html", {"page": "login", "error": error})


@auth_router.post("/logout", name="auth.logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url=request.url_for("auth.login_page"), status_code=status.HTTP_303_SEE_OTHER)


@auth_router.get("/api/session")
async def api_session(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"ok": False, "error": "Chưa đăng nhập"})
    return {"ok": True, "user": user.to_dict()}

from typing import Optional
