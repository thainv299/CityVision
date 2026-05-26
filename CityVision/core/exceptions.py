from fastapi import Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from core.config import templates
from core.security import get_current_user_from_request

async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    if exc.status_code == status.HTTP_303_SEE_OTHER:
        return RedirectResponse(url=exc.headers.get("Location", "/login"))
    if request.url.path.startswith("/api/"):
        return JSONResponse({"ok": False, "error": exc.detail}, status_code=exc.status_code)
        
    title, message = "", ""
    if exc.status_code == 403:
        title = "Không có quyền truy cập"
        message = exc.detail if exc.detail != "Forbidden" else "Tài khoản hiện tại không được phép mở trang này."
    elif exc.status_code == 404:
        title = "Không tìm thấy nội dung"
        message = "Đường dẫn bạn mở không tồn tại trong web portal."
    elif exc.status_code == 413:
        title = "File quá lớn"
        message = "Video upload vượt quá giới hạn dung lượng cho phép."
        
    return templates.TemplateResponse(
        request=request,
        name="error.html",
        context={"request": request, "page": "error", "status_code": exc.status_code, "title": title, "message": message, "current_user": get_current_user_from_request(request)},
        status_code=exc.status_code
    )
