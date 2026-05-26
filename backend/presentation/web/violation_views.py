from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import RedirectResponse, JSONResponse

from presentation.container import container
from presentation.middlewares.auth import login_required

violation_router = APIRouter()

@violation_router.get("/violations", name="violations.violations_page")
def violations_page(request: Request, user=Depends(login_required)):
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

@violation_router.get("/violations/search", name="violations.violation_search_page")
def violation_search_page(request: Request, user=Depends(login_required)):
    """Trang tìm kiếm vi phạm đỗ xe nâng cao"""
    if isinstance(user, RedirectResponse):
        return user
    
    return container.render_template(
        request,
        "violation_search.html",
        {
            "page": "violation-search",
        }
    )

@violation_router.get("/api/violations")
def api_violations(
    request: Request,
    user=Depends(login_required),
    page: int = 1,
    limit: int = 30
):
    """Lấy danh sách vi phạm đỗ xe có phân trang và bộ lọc"""
    filter_type = request.query_params.get("filter")
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
    from database.sqlite_db import get_illegal_parking_violations, get_total_records_count
    violations = get_illegal_parking_violations(limit, offset, filter_type, date, hour, camera_id, record_id)
    
    # Tính tổng để phân trang (với bộ lọc)
    conds = []
    params = []
    if filter_type == "has_plate":
        conds.append("(bien_so IS NOT NULL AND bien_so != '' AND bien_so NOT LIKE '%Không%' AND bien_so NOT LIKE 'ID_%')")
    elif filter_type == "no_plate":
        conds.append("(bien_so IS NULL OR bien_so = '' OR bien_so LIKE '%Không%' OR bien_so LIKE 'ID_%')")
    if date:
        conds.append("DATE(thoi_gian_vi_pham) = ?")
        params.append(date)
    if hour:
        conds.append("strftime('%H', thoi_gian_vi_pham) = ?")
        params.append(hour.zfill(2))
    if camera_id is not None:
        conds.append("id_camera = ?")
        params.append(camera_id)
    if record_id is not None:
        conds.append("id = ?")
        params.append(record_id)

    total_all = get_total_records_count("vi_pham_do_xe", " AND ".join(conds) if conds else "", params)
    return {
        "ok": True,
        "total": total_all,
        "page": page,
        "limit": limit,
        "violations": violations,
        "filter": filter_type,
    }

@violation_router.post("/api/violations/{violation_id}/resolve")
def api_resolve_violation(
    violation_id: int,
    user=Depends(login_required)
):
    """Đánh dấu vi phạm đã giải quyết"""
    from database.sqlite_db import resolve_parking_violation
    success = resolve_parking_violation(violation_id)
    if success:
        return {"ok": True}
    return JSONResponse(status_code=400, content={"ok": False, "error": "Khong the danh dau. Vi pham khong ton tai hoac da xu ly."})

@violation_router.get("/api/thumbnail")
def api_thumbnail(path: str, width: int = 300):
    """Tạo và trả về ảnh thu nhỏ (thumbnail) siêu nhẹ để load danh sách nhanh"""
    import os
    import cv2
    from fastapi import Response
    from core.config import PROJECT_ROOT
    try:
        # Chống tấn công Directory Traversal
        if ".." in path or path.startswith("/"):
            return Response(status_code=400)
            
        full_path = str(PROJECT_ROOT / path)
        if not os.path.exists(full_path):
            return Response(status_code=404)
            
        img = cv2.imread(full_path)
        if img is None:
            return Response(status_code=404)
            
        h, w = img.shape[:2]
        ratio = width / float(w)
        new_h = int(h * ratio)
        
        resized = cv2.resize(img, (width, new_h), interpolation=cv2.INTER_AREA)
        _, buffer = cv2.imencode('.jpg', resized, [int(cv2.IMWRITE_JPEG_QUALITY), 60])
        
        # Bật Cache-Control để trình duyệt và Cloudflare tự động lưu đệm 1 tuần
        return Response(
            content=buffer.tobytes(), 
            media_type="image/jpeg",
            headers={"Cache-Control": "public, max-age=604800"}
        )
    except Exception as e:
        return Response(status_code=500)

