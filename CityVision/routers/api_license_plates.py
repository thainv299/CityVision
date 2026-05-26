from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from core.security import require_login
from frontend.database import (
    list_detected_license_plates,
    list_license_plates_by_date,
    log_detected_license_plate,
)

router = APIRouter(prefix="/api/license-plates")


@router.get("")
def list_plates(limit: int = 100, offset: int = 0, user=Depends(require_login)):
    """Lấy danh sách biển số phát hiện"""
    try:
        plates = list_detected_license_plates(limit=limit, offset=offset)
        return {"ok": True, "plates": plates, "count": len(plates)}
    except Exception as e:
        return JSONResponse(
            {"ok": False, "error": str(e)},
            status_code=500,
        )


@router.get("/date/{detected_date}")
def list_plates_by_date(detected_date: str, user=Depends(require_login)):
    """Lấy danh sách biển số theo ngày (format: YYYY-MM-DD)"""
    try:
        plates = list_license_plates_by_date(detected_date)
        return {"ok": True, "plates": plates, "count": len(plates)}
    except Exception as e:
        return JSONResponse(
            {"ok": False, "error": str(e)},
            status_code=500,
        )


@router.post("")
def log_plate(
    license_plate: str,
    detected_date: str,
    avg_confidence: float = 0.0,
    image_paths: str = None,
    user=Depends(require_login),
):
    """Ghi lại một biển số phát hiện"""
    if not license_plate:
        return JSONResponse(
            {"ok": False, "error": "license_plate is required"},
            status_code=400,
        )
    if not detected_date:
        return JSONResponse(
            {"ok": False, "error": "detected_date is required"},
            status_code=400,
        )

    try:
        plate = log_detected_license_plate(
            license_plate=license_plate,
            detected_date=detected_date,
            avg_confidence=avg_confidence,
            image_paths=image_paths,
        )
        return {"ok": True, "plate": plate}
    except Exception as e:
        return JSONResponse(
            {"ok": False, "error": str(e)},
            status_code=500,
        )
