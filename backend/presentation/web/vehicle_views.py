from fastapi import APIRouter, Request, Depends, UploadFile, File, Form
from fastapi.responses import RedirectResponse

from presentation.container import container
from presentation.middlewares.auth import login_required

vehicle_router = APIRouter()

@vehicle_router.get("/vehicles", name="vehicles.vehicles_page")
def vehicles_page(request: Request, user=Depends(login_required)):
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

@vehicle_router.get("/vehicles/search", name="vehicles.search_page")
def search_page(request: Request, user=Depends(login_required)):
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
def api_vehicles(
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

@vehicle_router.get("/api/vehicles/date/{detected_date}")
def api_vehicles_by_date(
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

@vehicle_router.get("/api/vehicles/suggestions")
def api_search_suggestions(
    request: Request,
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


@vehicle_router.get("/vehicles/image-search", name="vehicles.image_search_page")
def image_search_page(request: Request, user=Depends(login_required)):
    """Trang tìm kiếm bằng hình ảnh (Image Similarity Search)"""
    if isinstance(user, RedirectResponse):
        return user
    return container.render_template(
        request,
        "image_search.html",
        {"page": "image-search"}
    )


@vehicle_router.post("/api/image-search")
def api_image_search(
    file: UploadFile = File(...),
    top_k: int = Form(12),
    search_scope: str = Form("all"),
    min_score: float = Form(0.3),
    user=Depends(login_required)
):
    """Tìm kiếm ảnh tương tự bằng ResNet50 GPU."""
    if isinstance(user, RedirectResponse):
        return user

    import sys
    from pathlib import Path
    PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))

    # Đọc dữ liệu ảnh
    image_bytes = file.file.read()
    if len(image_bytes) == 0:
        from fastapi.responses import JSONResponse
        return JSONResponse({"ok": False, "detail": "File rỗng"}, status_code=400)

    scope_map = {
        "all": None,
        "plates": ["plates"],
        "violations": ["violations"],
        "congestion": ["traffic"], 
    }
    search_dirs = scope_map.get(search_scope)

    try:
        from modules.image_search.image_search_service import get_image_search_service
        service = get_image_search_service()
        
        # Thực hiện tìm kiếm tương đồng
        results = service.search_similar(
            query_image=image_bytes,
            top_k=min(top_k, 50),
            search_dirs=search_dirs,
            min_score=min_score,
        )

        from database.sqlite_db import connect
        with connect() as conn:
            for res in results:
                img_path = res["path"]
                row = conn.execute("""
                    SELECT b.bien_so, b.ngay_phat_hien, b.thoi_gian_phat_hien, c.ten_camera
                    FROM bien_so_phat_hien b
                    LEFT JOIN camera c ON b.id_camera = c.id
                    WHERE b.duong_dan_anh LIKE ?
                    LIMIT 1
                """, (f"%{img_path}%",)).fetchone()
                
                if row:
                    res["license_plate"] = row["bien_so"]
                    res["detected_date"] = row["ngay_phat_hien"]
                    res["detected_time"] = row["thoi_gian_phat_hien"]
                    res["camera_name"] = row["ten_camera"]
                else:
                    res["license_plate"] = "N/A"
                    res["detected_date"] = "N/A"
                    res["detected_time"] = "N/A"
                    res["camera_name"] = "Unknown"
        
        return {
            "ok": True,
            "total": len(results),
            "search_scope": search_scope,
            "results": results,
        }
    except Exception as e:
        from fastapi.responses import JSONResponse
        return JSONResponse({"ok": False, "detail": str(e)}, status_code=500)


@vehicle_router.get("/api/image-search/status")
def api_image_search_status(request: Request, user=Depends(login_required)):
    """Trạng thái service (GPU/CPU, số ảnh cache)."""
    if isinstance(user, RedirectResponse):
        return user
    try:
        import sys
        from pathlib import Path
        PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
        if str(PROJECT_ROOT) not in sys.path:
            sys.path.insert(0, str(PROJECT_ROOT))
        from modules.image_search.image_search_service import get_image_search_service
        service = get_image_search_service()
        return {"ok": True, "status": service.get_cache_stats()}
    except Exception as e:
        return {"ok": False, "status": {"cached_images": 0, "device": "unavailable", "logs_dir": "N/A"}}
