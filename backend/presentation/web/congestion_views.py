from fastapi import APIRouter, Request, Depends
from fastapi.responses import RedirectResponse

from presentation.container import container
from presentation.middlewares.auth import login_required

congestion_router = APIRouter()

@congestion_router.get("/congestion", name="congestion.congestion_page")
def congestion_page(request: Request, user=Depends(login_required)):
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

@congestion_router.get("/congestion/search", name="congestion.congestion_search_page")
def congestion_search_page(request: Request, user=Depends(login_required)):
    """Trang tìm kiếm nhật ký ùn tắc nâng cao"""
    if isinstance(user, RedirectResponse):
        return user
    
    return container.render_template(
        request,
        "congestion_search.html",
        {
            "page": "congestion-search",
        }
    )

@congestion_router.get("/api/congestion")
def api_congestion(
    request: Request,
    user=Depends(login_required),
    page: int = 1,
    limit: int = 30
):
    """Lấy danh sách nhật ký ùn tắc có phân trang và bộ lọc"""
    level = request.query_params.get("level")
    date = request.query_params.get("date")
    hour = request.query_params.get("hour")
    camera_id = request.query_params.get("camera_id")

    if level:
        try: level = int(level)
        except: level = None
    if camera_id:
        try: camera_id = int(camera_id)
        except: camera_id = None

    offset = (page - 1) * limit
    from database.sqlite_db import get_congestion_history, get_total_records_count
    logs = get_congestion_history(limit, offset, level, date, hour, camera_id)
    
    # Tính tổng để phân trang (với bộ lọc)
    conds = []
    params = []
    if level is not None:
        conds.append("muc_do_un_tac = ?")
        params.append(level)
    if date:
        conds.append("DATE(thoi_gian_bat_dau) = ?")
        params.append(date)
    if hour:
        conds.append("strftime('%H', thoi_gian_bat_dau) = ?")
        params.append(hour.zfill(2))
    if camera_id is not None:
        conds.append("id_camera = ?")
        params.append(camera_id)

    total_all = get_total_records_count("nhat_ky_un_tac", " AND ".join(conds) if conds else "", params)
    return {
        "ok": True,
        "total": total_all,
        "page": page,
        "limit": limit,
        "logs": logs,
        "level": level,
    }
