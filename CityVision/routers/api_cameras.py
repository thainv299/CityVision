import cv2
import sqlite3
from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse

from core.utils import json_error, validate_camera_payload, normalize_capture_source
from core.security import require_login
from services.camera_service import build_placeholder_frame, prepare_snapshot_frame, encode_jpeg
from frontend.database import (
    get_camera,
    list_cameras,
    create_camera,
    update_camera,
    delete_camera
)

router = APIRouter(prefix="/api/cameras")

@router.get("")
def api_list_cameras(user=Depends(require_login)):
    return {"ok": True, "cameras": list_cameras()}

@router.post("")
def api_create_camera(payload: dict, user=Depends(require_login)):
    try:
        camera_payload = validate_camera_payload(payload)
        created = create_camera(camera_payload)
        return JSONResponse({"ok": True, "camera": created}, status_code=201)
    except ValueError as exc:
        return json_error(str(exc), 400)
    except sqlite3.IntegrityError:
        return json_error("Tên camera đã tồn tại.", 409)

@router.put("/{camera_id}")
def api_update_camera(camera_id: int, payload: dict, user=Depends(require_login)):
    if get_camera(camera_id) is None:
        return json_error("Không tìm thấy camera.", 404)
    try:
        camera_payload = validate_camera_payload(payload)
        updated = update_camera(camera_id, camera_payload)
        if updated is None:
            return json_error("Không tìm thấy camera.", 404)
        return {"ok": True, "camera": updated}
    except ValueError as exc:
        return json_error(str(exc), 400)
    except sqlite3.IntegrityError:
        return json_error("Tên camera đã tồn tại.", 409)

@router.delete("/{camera_id}")
def api_delete_camera(camera_id: int, user=Depends(require_login)):
    deleted = delete_camera(camera_id)
    if not deleted:
        return json_error("Không tìm thấy camera.", 404)
    return {"ok": True}

@router.get("/{camera_id}/snapshot")
def api_camera_snapshot(camera_id: int, user=Depends(require_login)):
    camera = get_camera(camera_id)
    if camera is None:
        return Response(content=build_placeholder_frame("Không tìm thấy camera."), media_type="image/jpeg")

    capture_source = normalize_capture_source(camera["stream_source"])
    if capture_source is None:
        return Response(
            content=build_placeholder_frame("Chưa cấu hình nguồn camera.", camera["name"]),
            media_type="image/jpeg",
        )

    capture = cv2.VideoCapture(capture_source)
    try:
        success, frame = capture.read()
    finally:
        capture.release()

    if not success or frame is None:
        detail = camera["stream_source"] or "Nguồn camera trống."
        return Response(
            content=build_placeholder_frame("Không đọc được hình ảnh camera.", detail),
            media_type="image/jpeg",
        )

    prepared = prepare_snapshot_frame(frame, camera)
    return Response(content=encode_jpeg(prepared), media_type="image/jpeg")
