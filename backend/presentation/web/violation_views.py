from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import RedirectResponse, JSONResponse

from presentation.container import container
from presentation.middlewares.auth import login_required

violation_router = APIRouter()

@violation_router.get("/violations", name="violations.violations_page")
async def violations_page(request: Request, user=Depends(login_required)):
    """Trang quản lý vi phạm đỗ xe"""
    if isinstance(user, RedirectResponse):
        return user
    
    return container.render_template(
        request,
        "violations.html",
        {
            "page": "violations",
        }
    )

@violation_router.get("/api/violations")
async def api_violations(
    user=Depends(login_required)
):
    """Lấy danh sách vi phạm đỗ xe chưa giải quyết"""
    from database.sqlite_db import get_illegal_parking_violations
    violations = get_illegal_parking_violations()
    return {
        "ok": True,
        "total": len(violations),
        "violations": violations,
    }

@violation_router.post("/api/violations/{violation_id}/resolve")
async def api_resolve_violation(
    violation_id: int,
    user=Depends(login_required)
):
    """Đánh dấu vi phạm đã giải quyết"""
    from database.sqlite_db import resolve_parking_violation
    success = resolve_parking_violation(violation_id)
    if success:
        return {"ok": True}
    return JSONResponse(status_code=400, content={"ok": False, "error": "Khong the danh dau. Vi pham khong ton tai hoac da xu ly."})
