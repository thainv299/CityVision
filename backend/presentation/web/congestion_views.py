from fastapi import APIRouter, Request, Depends
from fastapi.responses import RedirectResponse

from presentation.container import container
from presentation.middlewares.auth import login_required

congestion_router = APIRouter()

@congestion_router.get("/congestion", name="congestion.congestion_page")
async def congestion_page(request: Request, user=Depends(login_required)):
    """Trang nhật ký ùn tắc"""
    if isinstance(user, RedirectResponse):
        return user
    
    return container.render_template(
        request,
        "congestion.html",
        {
            "page": "congestion",
        }
    )

@congestion_router.get("/api/congestion")
async def api_congestion(
    user=Depends(login_required),
    page: int = 1,
    limit: int = 30
):
    """Lấy danh sách nhật ký ùn tắc có phân trang"""
    offset = (page - 1) * limit
    from database.sqlite_db import get_congestion_history, get_total_records_count
    logs = get_congestion_history(limit, offset)
    total_all = get_total_records_count("nhat_ky_un_tac")
    return {
        "ok": True,
        "total": total_all,
        "page": page,
        "limit": limit,
        "logs": logs,
    }
