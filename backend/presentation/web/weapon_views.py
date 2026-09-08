from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import RedirectResponse, JSONResponse

from presentation.container import container
from presentation.middlewares.auth import login_required

weapon_router = APIRouter()

@weapon_router.get("/weapons", name="weapons.weapons_page")
def weapons_page(request: Request, user=Depends(login_required)):
    """Trang quản lý phát hiện vũ khí"""
    if isinstance(user, RedirectResponse):
        return user
    
    return container.render_template(
        request,
        "weapons.html",
        {
            "page": "weapons",
        }
    )

@weapon_router.get("/weapons/search", name="weapons.weapon_search_page")
def weapon_search_page(request: Request, user=Depends(login_required)):
    """Trang tìm kiếm nhật ký phát hiện vũ khí nâng cao"""
    if isinstance(user, RedirectResponse):
        return user
    
    return container.render_template(
        request,
        "weapon_search.html",
        {
            "page": "weapon-search",
        }
    )

@weapon_router.get("/api/weapons")
def api_weapons(
    request: Request,
    user=Depends(login_required),
    page: int = 1,
    limit: int = 30
):
    """Lấy danh sách sự kiện phát hiện vũ khí có phân trang và bộ lọc"""
    weapon_type = request.query_params.get("weapon_type")
    date = request.query_params.get("date")
    hour = request.query_params.get("hour")
    camera_id = request.query_params.get("camera_id")
    record_id = request.query_params.get("id")

    if camera_id:
        try: camera_id = int(camera_id)
        except: camera_id = None
    if record_id:
        try: record_id = int(record_id)
        except: record_id = None

    offset = (page - 1) * limit
    accessible_cameras = container.camera_use_cases.list_cameras_for_user(user)
    accessible_ids = [c.id for c in accessible_cameras]
    
    from database.sqlite_db import get_weapon_detections, get_total_records_count
    
    if not accessible_ids:
        return {
            "ok": True, "total": 0, "page": page, "limit": limit,
            "weapons": [], "weapon_type": weapon_type
        }

    weapons = get_weapon_detections(
        limit=limit,
        offset=offset,
        weapon_type=weapon_type,
        date=date,
        hour=hour,
        camera_id=camera_id,
        record_id=record_id,
        allowed_camera_ids=accessible_ids
    )
    
    # Tính tổng số bản ghi phù hợp để phân trang
    conds = []
    params = []
    
    placeholders = ','.join('?' * len(accessible_ids))
    conds.append(f"id_camera IN ({placeholders})")
    params.extend(accessible_ids)
    
    if weapon_type:
        conds.append("loai_vu_khi = ?")
        params.append(weapon_type)
    if date:
        conds.append("DATE(thoi_gian_phat_hien) = ?")
        params.append(date)
    if hour:
        conds.append("strftime('%H', thoi_gian_phat_hien) = ?")
        params.append(hour.zfill(2))
    if camera_id is not None:
        conds.append("id_camera = ?")
        params.append(camera_id)
    if record_id is not None:
        conds.append("id = ?")
        params.append(record_id)

    total_all = get_total_records_count("phat_hien_vu_khi", " AND ".join(conds) if conds else "", params)
    return {
        "ok": True,
        "total": total_all,
        "page": page,
        "limit": limit,
        "weapons": weapons,
        "weapon_type": weapon_type,
    }

@weapon_router.post("/api/weapons/{weapon_id}/resolve")
def api_resolve_weapon(
    weapon_id: int,
    user=Depends(login_required)
):
    """Đánh dấu sự kiện phát hiện vũ khí đã giải quyết"""
    from database.sqlite_db import resolve_weapon_detection
    success = resolve_weapon_detection(weapon_id)
    if success:
        return {"ok": True}
    return JSONResponse(status_code=400, content={"ok": False, "error": "Không thể đánh dấu. Bản ghi không tồn tại hoặc đã xử lý."})

@weapon_router.delete("/api/weapons/{weapon_id}")
def api_delete_weapon(
    weapon_id: int,
    user=Depends(login_required)
):
    """Xóa sự kiện phát hiện vũ khí"""
    from database.sqlite_db import delete_weapon_detection
    success = delete_weapon_detection(weapon_id)
    if success:
        return {"ok": True}
    return JSONResponse(status_code=400, content={"ok": False, "error": "Không thể xóa bản ghi."})
