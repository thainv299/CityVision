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
    limit: int = 100,
):
    """Lấy danh sách phương tiện/biển số được phát hiện với bộ lọc"""
    filter_type = request.query_params.get("filter")
    search_query = request.query_params.get("search")
    
    from database.sqlite_db import get_detected_license_plates
    plates = get_detected_license_plates(limit, filter_type, search_query)
    return {
        "ok": True,
        "total": len(plates),
        "plates": plates,
        "filter": filter_type,
        "search": search_query
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
        suggestions.append({
            "text": plate["license_plate"],
            "type": "plate",
            "sub": f"Phát hiện ngày {plate['detected_date']}"
        })
        
    return {"ok": True, "suggestions": suggestions[:10]}
