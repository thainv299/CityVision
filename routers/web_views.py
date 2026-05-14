from fastapi import APIRouter, Request, Form, Depends, status
from fastapi.responses import HTMLResponse, RedirectResponse
from werkzeug.security import check_password_hash

from core.config import DEFAULT_MODEL_PATH, templates
from core.security import get_current_user_from_request, require_login, require_admin
from frontend.database import (
    get_user_record_by_username,
    get_dashboard_stats,
    list_cameras,
)

router = APIRouter()

def render(request: Request, template_name: str, context: dict = None):
    if context is None: context = {}
    context["request"] = request
    context["current_user"] = get_current_user_from_request(request)
    return templates.TemplateResponse(request=request, name=template_name, context=context)

@router.get("/")
def index(request: Request):
    if get_current_user_from_request(request) is not None:
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

@router.get("/login", response_class=HTMLResponse, name="login")
def login_get(request: Request):
    if get_current_user_from_request(request) is not None:
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    return render(request, "login.html", {"page": "login", "error": None})

@router.post("/login", response_class=HTMLResponse, name="login")
def login_post(request: Request, username: str = Form(""), password: str = Form("")):
    username = username.strip()
    user_record = get_user_record_by_username(username)
    if user_record is None or not check_password_hash(user_record["password_hash"], password):
        error = "Tên đăng nhập hoặc mật khẩu không đúng."
        return render(request, "login.html", {"page": "login", "error": error})
    elif not bool(user_record["is_active"]):
        error = "Tài khoản này đang bị khóa."
        return render(request, "login.html", {"page": "login", "error": error})
    else:
        request.session.clear()
        request.session["user_id"] = int(user_record["id"])
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)

@router.post("/logout", name="logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, user=Depends(require_login)):
    return render(request, "dashboard.html", {"page": "dashboard", "stats": get_dashboard_stats()})

@router.get("/users", response_class=HTMLResponse)
def users_page(request: Request, user=Depends(require_admin)):
    return render(request, "users.html", {"page": "users"})

@router.get("/cameras", response_class=HTMLResponse)
def cameras_page(request: Request, user=Depends(require_login)):
    return render(request, "cameras.html", {"page": "cameras"})

@router.get("/test-video", response_class=HTMLResponse)
def test_video_page(request: Request, user=Depends(require_login)):
    return render(request, "test_video.html", {
        "page": "test-video",
        "cameras": list_cameras(),
        "default_model_path": str(DEFAULT_MODEL_PATH),
    })

@router.get("/license-plates", response_class=HTMLResponse)
def license_plates_page(request: Request, user=Depends(require_login)):
    return render(request, "license_plates.html", {"page": "license-plates"})
