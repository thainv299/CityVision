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

@vehicle_router.get("/api/vehicles")
async def api_vehicles(
    user=Depends(login_required),
    limit: int = 100,
    plate: str = None,
):
    """Lấy danh sách phương tiện/biển số được phát hiện"""
    from database.sqlite_db import get_detected_license_plates
    plates = get_detected_license_plates(limit, license_plate=plate)
    return {
        "ok": True,
        "total": len(plates),
        "plates": plates,
    }

@vehicle_router.get("/api/vehicles/date/{detected_date}")
async def api_vehicles_by_date(
    detected_date: str,
    user=Depends(login_required),
):
    """Lấy phương tiện/biển số được phát hiện trong ngày cụ thể"""
    if isinstance(user, RedirectResponse):
        return user
    
    from database.sqlite_db import get_license_plate_by_date
    plates = get_license_plate_by_date(detected_date)
    return {
        "ok": True,
        "date": detected_date,
        "total": len(plates),
        "plates": plates,
    }
