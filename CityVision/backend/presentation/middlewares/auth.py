from fastapi import Request, HTTPException, status
from fastapi.responses import RedirectResponse, JSONResponse
from typing import Optional


def is_api_request(request: Request) -> bool:
    return request.url.path.startswith("/api/")


def get_current_user(request: Request):
    return getattr(request.state, "current_user", None)


async def login_required(request: Request):
    user = get_current_user(request)
    if user is None:
        if is_api_request(request):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Bạn cần đăng nhập để tiếp tục."
            )
        return RedirectResponse(url=request.url_for("auth.login_page"), status_code=status.HTTP_303_SEE_OTHER)
    return user


async def admin_required(request: Request):
    user = get_current_user(request)
    if user is None:
        if is_api_request(request):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Bạn cần đăng nhập để tiếp tục."
            )
        return RedirectResponse(url=request.url_for("auth.login_page"), status_code=status.HTTP_303_SEE_OTHER)
    
    if not user.is_admin():
        if is_api_request(request):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Bạn không có quyền truy cập chức năng này."
            )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Không có quyền truy cập")
    return user
