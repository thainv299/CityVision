import uuid
import time
import asyncio
from io import BytesIO
from pathlib import Path
from fastapi import APIRouter, Request, Depends, UploadFile, HTTPException
from fastapi.responses import JSONResponse, FileResponse, Response, StreamingResponse
from werkzeug.utils import secure_filename

from core.config import executor, OUTPUTS_DIR, ALLOWED_VIDEO_EXTENSIONS
from core.utils import json_error, resolve_path, build_test_settings
from core.security import require_login
from frontend.database import get_camera
from services.job_manager import (
    set_job,
    get_job,
    public_job,
    run_detection_job
)


router = APIRouter()

async def stream_job(job_id: str):
    """MJPEG stream generator. Khi client ngat ket noi, finally block se abort job."""
    try:
        while True:
            job = get_job(job_id)
            if not job:
                break
            
            frame = job.get("latest_frame")
            if frame:
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
            
            status = job.get("status")
            if status in ("completed", "failed", "aborted"):
                break
            
            await asyncio.sleep(0.03)
    except (asyncio.CancelledError, GeneratorExit):
        pass
    finally:
        # Khi client ngat ket noi (F5, tat tab), abort job dang chay
        job = get_job(job_id)
        if job and job.get("status") in ("queued", "running"):
            set_job(job_id, status="aborted", message="Job da bi huy do mat ket noi stream.")

@router.get("/api/test-jobs/{job_id}/stream")
def serve_test_job_stream(job_id: str):
    return StreamingResponse(
        stream_job(job_id), 
        media_type="multipart/x-mixed-replace; boundary=frame"
    )


@router.post("/api/test-jobs")
async def api_create_test_job(request: Request, user=Depends(require_login)):
    form = await request.form()
    job_id = uuid.uuid4().hex
    camera = None
    camera_id_text = str(form.get("camera_id", "")).strip()
    if camera_id_text:
        camera = get_camera(int(camera_id_text))
        if camera is None:
            return json_error("Camera duoc chon khong ton tai.", 404)

    upload_file = form.get("video_file")

    # Kiem tra duck-typing vi UploadFile co the den tu starlette.datastructures hoac fastapi.UploadFile
    # tuoc do gay loi isinstance
    is_valid_file = upload_file is not None and hasattr(upload_file, "filename") and hasattr(upload_file, "read")

    if is_valid_file and upload_file.filename:
        extension = Path(upload_file.filename).suffix.lower()
        if extension not in ALLOWED_VIDEO_EXTENSIONS:
            return json_error("Dinh dang video khong duoc ho tro.", 400)

        # Read video bytes into memory (BytesIO) - NO FILE SAVE
        file_bytes = await upload_file.read()
        video_stream = BytesIO(file_bytes)
    else:
        return json_error("Hay chon file upload hop le.", 400)

    try:
        form_data = {k: v for k, v in form.multi_items()}
        test_settings = build_test_settings(form_data, camera)
        test_settings["camera_id"] = camera["id"] if camera else 0
    except ValueError as exc:
        return json_error(str(exc), 400)

    output_filename = f"{job_id}_result.mp4"
    submitted_at = time.time()
    set_job(
        job_id,
        status="queued",
        message="Da nhan yeu cau. Job se duoc xu ly theo hang doi.",
        error=None,
        output_filename=None,
        summary=None,
        source_video=None,  # No local file path
        submitted_at=submitted_at,
        progress={
            "phase": "queued",
            "processed_frames": 0,
            "source_total_frames": None,
            "progress_percent": 0.0,
            "elapsed_seconds": 0.0,
            "latest_status": "Dang cho den luot xu ly...",
        },
    )

    # Pass BytesIO stream instead of file path
    executor.submit(
        run_detection_job,
        job_id,
        video_stream,
        extension,
        output_filename,
        test_settings,
    )

    return JSONResponse({"ok": True, "job": public_job(get_job(job_id) or {"id": job_id})}, status_code=202)

@router.get("/api/test-jobs/{job_id}")
def api_get_test_job(job_id: str, user=Depends(require_login)):
    job = get_job(job_id)
    if job is None:
        return json_error("Không tìm thấy job kiểm tra.", 404)
    return {"ok": True, "job": public_job(job)}

@router.post("/api/test-jobs/{job_id}/stop")
def api_stop_test_job(job_id: str, user=Depends(require_login)):
    job = get_job(job_id)
    if job is None:
        return json_error("Không tìm thấy job kiểm tra.", 404)
    if job.get("status") in ("queued", "running"):
        set_job(job_id, status="aborted", message="Đang dừng job...")
        return {"ok": True, "message": "Đã gửi yêu cầu dừng job."}
    return json_error(f"Không thể dừng job ở trạng thái {job.get('status')}", 400)
