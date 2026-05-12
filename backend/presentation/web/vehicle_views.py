from fastapi import APIRouter, Request, Depends
from fastapi.responses import RedirectResponse

from presentation.container import container
from presentation.middlewares.auth import login_required

vehicle_router = APIRouter()

@vehicle_router.get("/vehicles", name="vehicles.vehicles_page")
async def vehicles_page(request: Request, user=Depends(login_required)):
    """Trang quản lý phương tiện (biển số)"""
    if isinstance(user, RedirectResponse):
        return user
    
    return container.render_template(
        request,
        "license_plates.html",
        {
            "page": "vehicles",
        }
    )

@vehicle_router.get("/search", name="vehicles.search_page")
async def search_page(request: Request, user=Depends(login_required)):
    """Trang tìm kiếm tập trung"""
    if isinstance(user, RedirectResponse):
        return user
    
    return container.render_template(
        request,
        "search.html",
        {
            "page": "search",
        }
    )

@vehicle_router.get("/api/vehicles")
async def api_vehicles(
    request: Request,
    user=Depends(login_required),
    limit: int = 30,
    page: int = 1
):
    """Lấy danh sách phương tiện/biển số được phát hiện với bộ lọc và phân trang"""
    filter_type = request.query_params.get("filter")
    search_query = request.query_params.get("search")
    camera_id = request.query_params.get("camera_id")
    if camera_id:
        try: camera_id = int(camera_id)
        except: camera_id = None
        
    offset = (page - 1) * limit
    
    from database.sqlite_db import get_detected_license_plates, get_total_records_count
    plates = get_detected_license_plates(limit, offset, filter_type, search_query, camera_id)
    
    # Tính tổng để phân trang
    conds = []
    params = []
    if filter_type == "has_plate":
        conds.append("(bien_so != 'Không phát hiện biển số xe' AND bien_so != '' AND bien_so IS NOT NULL)")
    elif filter_type == "no_plate":
        conds.append("(bien_so = 'Không phát hiện biển số xe' OR bien_so = '' OR bien_so IS NULL)")
    if search_query:
        conds.append("bien_so LIKE ?")
        params.append(f"%{search_query}%")
    if camera_id is not None:
        conds.append("id_camera = ?")
        params.append(camera_id)
        
    total_all = get_total_records_count("bien_so_phat_hien", " AND ".join(conds) if conds else "", params)
    
    return {
        "ok": True,
        "total": total_all,
        "page": page,
        "limit": limit,
        "plates": plates,
        "filter": filter_type,
        "search": search_query,
        "camera_id": camera_id
    }

@vehicle_router.get("/api/violations")
async def api_violations(
    user=Depends(login_required),
    page: int = 1,
    limit: int = 30
):
    """Lấy danh sách vi phạm đỗ xe có phân trang"""
    offset = (page - 1) * limit
    from database.sqlite_db import get_illegal_parking_violations, get_total_records_count
    violations = get_illegal_parking_violations(limit, offset)
    total_all = get_total_records_count("vi_pham_do_xe")
    return {
        "ok": True,
        "total": total_all,
        "page": page,
        "limit": limit,
        "violations": violations,
    }

@vehicle_router.get("/api/vehicles/date/{detected_date}")
async def api_vehicles_by_date(
    detected_date: str,
    user=Depends(login_required),
):
    """Lấy phương tiện/biển số được phát hiện trong ngày cụ thể"""
    from database.sqlite_db import get_license_plate_by_date
    plates = get_license_plate_by_date(detected_date)
    return {
        "ok": True,
        "date": detected_date,
        "total": len(plates),
        "plates": plates,
    }
@vehicle_router.delete("/api/vehicles/{record_id}")
async def delete_vehicle(
    record_id: int,
    user=Depends(login_required),
):
    """Xóa một bản ghi phương tiện"""
    from database.sqlite_db import delete_license_plate_record
    success = delete_license_plate_record(record_id)
    return {"ok": success}

@vehicle_router.get("/api/search/suggestions")
async def api_search_suggestions(
    q: str = "",
    user=Depends(login_required),
):
    """Lấy gợi ý tìm kiếm (biển số, camera)"""
    if not q or len(q) < 2:
        return {"ok": True, "suggestions": []}
        
    from database.sqlite_db import global_search
    results = global_search(q)
    
    suggestions = []
    # Thêm camera vào gợi ý
    for cam in results.get("cameras", []):
        suggestions.append({
            "text": cam["ten_camera"],
            "type": "camera",
            "id": cam["id"],
            "sub": "Vị trí Camera"
        })
    # Thêm biển số vào gợi ý
    for plate in results.get("plates", []):
        img_path = plate.get("image_paths", "").split(',')[0] if plate.get("image_paths") else ""
        suggestions.append({
            "text": plate["license_plate"],
            "type": "plate",
            "img": img_path,
            "sub": f"Phát hiện ngày {plate['detected_date']}"
        })
        
    return {"ok": True, "suggestions": suggestions[:10]}
