"""
API Router cho Image Similarity Search.

Endpoints:
  POST /api/image-search          — Upload ảnh, trả về top-K ảnh tương tự
  GET  /api/image-search/status   — Trạng thái service (cache, device)
  POST /api/image-search/index    — Kích hoạt re-index toàn bộ ảnh
"""

import sys
from pathlib import Path

from fastapi import APIRouter, UploadFile, File, Depends, HTTPException, Form
from fastapi.responses import JSONResponse

# Thêm thư mục gốc project vào sys.path để import modules
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.security import require_login

router = APIRouter()


def _get_service():
    """Lấy singleton ImageSearchService, raise lỗi nếu torch chưa cài."""
    try:
        from modules.image_search.image_search_service import get_image_search_service
        return get_image_search_service()
    except ImportError as e:
        raise HTTPException(
            status_code=503,
            detail=f"Chức năng tìm kiếm bằng ảnh chưa sẵn sàng: {str(e)}. "
                   "Vui lòng cài: pip install torch torchvision pillow"
        )


@router.post("/api/image-search")
async def search_by_image(
    file: UploadFile = File(...),
    top_k: int = Form(default=12),
    search_scope: str = Form(default="all"),  # "all", "plates", "violations"
    min_score: float = Form(default=0.3),
    user=Depends(require_login),
):
    """
    Tìm ảnh tương tự trong hệ thống.
    
    - **file**: File ảnh upload (jpg, png, ...)
    - **top_k**: Số kết quả trả về (mặc định 12)
    - **search_scope**: Phạm vi tìm kiếm: "all" | "plates" | "violations" | "congestion"
    - **min_score**: Ngưỡng score tối thiểu 0.0–1.0 (mặc định 0.3)
    """
    # Kiểm tra định dạng file
    if file.content_type not in {"image/jpeg", "image/png", "image/bmp", "image/webp", "image/jpg"}:
        raise HTTPException(
            status_code=400,
            detail=f"Định dạng ảnh không hỗ trợ: {file.content_type}. "
                   "Chấp nhận: JPG, PNG, BMP, WebP"
        )

    # Đọc nội dung ảnh
    image_bytes = await file.read()
    if len(image_bytes) == 0:
        raise HTTPException(status_code=400, detail="File ảnh rỗng")

    # Giới hạn kích thước (10MB)
    if len(image_bytes) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Ảnh quá lớn (tối đa 10MB)")

    # Xác định thư mục tìm kiếm
    scope_map = {
        "all": None,
        "plates": ["plates"],
        "violations": ["violations"],
        "congestion": ["congestion"],
        "vehicles": ["vehicles"],
    }
    search_dirs = scope_map.get(search_scope, None)

    # Thực hiện tìm kiếm
    try:
        service = _get_service()
        results = service.search_similar(
            query_image=image_bytes,
            top_k=min(top_k, 50),      # Giới hạn tối đa 50
            search_dirs=search_dirs,
            min_score=min_score,
        )

        return {
            "ok": True,
            "total": len(results),
            "query_filename": file.filename,
            "search_scope": search_scope,
            "top_k": top_k,
            "min_score": min_score,
            "results": results,
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Lỗi khi tìm kiếm: {str(e)}"
        )


@router.get("/api/image-search/status")
async def get_service_status(user=Depends(require_login)):
    """Lấy trạng thái service: cache, device, logs_dir."""
    try:
        service = _get_service()
        stats = service.get_cache_stats()
        return {"ok": True, "status": stats}
    except HTTPException:
        return {
            "ok": False,
            "status": {
                "cached_images": 0,
                "device": "unavailable",
                "logs_dir": "N/A",
            }
        }


@router.post("/api/image-search/index")
async def trigger_reindex(
    force: bool = Form(default=False),
    user=Depends(require_login),
):
    """
    Kích hoạt index (tính embedding) cho toàn bộ ảnh trong logs/.
    
    - **force**: True = tính lại tất cả kể cả đã cache
    """
    try:
        service = _get_service()
        count = service.index_all_images(force_reindex=force)
        return {
            "ok": True,
            "indexed": count,
            "message": f"Đã index {count} ảnh thành công"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
