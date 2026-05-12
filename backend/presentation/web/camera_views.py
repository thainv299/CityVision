import os
import cv2
import uuid
import io
import subprocess
import tempfile
from pathlib import Path
from fastapi import APIRouter, Request, Depends, status, File, UploadFile, Form, Body
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from typing import Any, Dict, Optional
import base64
import numpy as np
from core.utils import resolve_path
from core.config import ALLOWED_VIDEO_EXTENSIONS, INPUTS_DIR, PROJECT_ROOT, DEFAULT_MODEL_PATH
from core.errors import AppError, NotFoundError
from presentation.container import container, templates
from presentation.middlewares.auth import login_required

camera_router = APIRouter()


@camera_router.get("/cameras", name="cameras.cameras_page")
async def cameras_page(request: Request, user=Depends(login_required)):
    if isinstance(user, RedirectResponse):
        return user
    
    # Scan models
    models_dir = PROJECT_ROOT / "models"
    available_models = []
    if models_dir.exists():
        for f in models_dir.iterdir():
            if f.is_file() and f.suffix.lower() in [".pt", ".engine"]:
                available_models.append(f.name)
    available_models.sort()

    return container.render_template(
        request, 
        "cameras.html", 
        {
            "page": "cameras", 
            "available_models": available_models,
            "default_model_path": str(DEFAULT_MODEL_PATH)
        }
    )


@camera_router.get("/api/cameras")
async def api_list_cameras(user=Depends(login_required)):
    cameras = container.camera_use_cases.list_cameras()
    return {"ok": True, "cameras": [c.to_dict() for c in cameras]}


@camera_router.get("/api/cameras/{camera_id}")
async def api_get_camera(camera_id: int, user=Depends(login_required)):
    try:
        camera = container.camera_use_cases.get_camera(camera_id)
        return {"ok": True, "camera": camera.to_dict()}
    except NotFoundError:
        return JSONResponse(status_code=404, content={"ok": False, "error": "Camera không tồn tại."})


@camera_router.post("/api/cameras/upload-source")
async def api_upload_camera_source(
    video_file: Optional[UploadFile] = File(None),
    upload_id: Optional[str] = Form(None),
    original_filename: Optional[str] = Form(None),
    user=Depends(login_required)
):
    import os
    import tempfile
    
    INPUTS_DIR.mkdir(parents=True, exist_ok=True)
    
    if upload_id:
        # Handle chunked upload completion
        temp_dir = Path(tempfile.gettempdir()) / "video_uploads"
        temp_file = temp_dir / f"{upload_id}.part"
        if not temp_file.exists():
            return JSONResponse(status_code=400, content={"ok": False, "error": "Không tìm thấy dữ liệu upload."})
        
        filename = original_filename or "video.mp4"
        suffix = Path(filename).suffix.lower()
        target_path = INPUTS_DIR / f"{uuid.uuid4().hex}{suffix}"
        
        # Move file from temp to inputs
        import shutil
        shutil.move(str(temp_file), str(target_path))
        
    elif video_file and video_file.filename:
        # Handle direct upload
        suffix = Path(video_file.filename).suffix.lower()
        if suffix not in ALLOWED_VIDEO_EXTENSIONS:
            return JSONResponse(status_code=400, content={"ok": False, "error": "Định dạng video không được hỗ trợ."})
            
        target_path = INPUTS_DIR / f"{uuid.uuid4().hex}{suffix}"
        try:
            content = await video_file.read()
            with target_path.open("wb") as handle:
                handle.write(content)
        except Exception:
            return JSONResponse(status_code=500, content={"ok": False, "error": "Không thể lưu file video."})
    else:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Không có file nào được gửi."})

    return {"ok": True, "path": str(target_path.resolve())}


@camera_router.post("/api/cameras")
async def api_create_camera(payload: Dict[str, Any], user=Depends(login_required)):
    try:
        created = container.camera_use_cases.create_camera(payload)
        return JSONResponse(status_code=status.HTTP_201_CREATED, content={"ok": True, "camera": created.to_dict()})
    except AppError as exc:
        return JSONResponse(status_code=exc.status_code, content={"ok": False, "error": exc.message})
    except Exception as e:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(e)})


@camera_router.put("/api/cameras/{camera_id}")
async def api_update_camera(camera_id: int, payload: Dict[str, Any], user=Depends(login_required)):
    try:
        # Kiểm tra trạng thái is_active trước và sau khi update
        is_active_requested = payload.get("is_active")
        
        updated = container.camera_use_cases.update_camera(camera_id, payload)
        
        # Đồng bộ luồng AI: Luôn dừng job cũ để áp dụng cấu hình mới nhất
        container.job_use_cases.stop_camera_jobs(camera_id)
        
        # Nếu camera đang ở trạng thái hoạt động, khởi động lại luồng nền
        if updated.is_active:
            container.job_use_cases.start_active_cameras(container.camera_use_cases)
            
        return {"ok": True, "camera": updated.to_dict()}
    except AppError as exc:
        return JSONResponse(status_code=exc.status_code, content={"ok": False, "error": exc.message})
    except Exception as e:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(e)})


@camera_router.delete("/api/cameras/{camera_id}")
async def api_delete_camera(camera_id: int, user=Depends(login_required)):
    try:
        container.camera_use_cases.delete_camera(camera_id)
        # Dừng toàn bộ luồng AI liên quan (nền + test)
        container.job_use_cases.stop_camera_jobs(camera_id)
        return {"ok": True}
    except AppError as exc:
        return JSONResponse(status_code=exc.status_code, content={"ok": False, "error": exc.message})
    except Exception as e:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(e)})


@camera_router.get("/api/cameras/{camera_id}/snapshot")
async def api_camera_snapshot(camera_id: int, raw: bool = False, user=Depends(login_required)):
    try:
        camera = container.camera_use_cases.get_camera(camera_id)
    except NotFoundError:
        from core.utils import build_placeholder_frame
        return StreamingResponse(io.BytesIO(build_placeholder_frame("Không tìm thấy camera.")), media_type="image/jpeg")
    from core.utils import build_placeholder_frame, encode_jpeg, normalize_capture_source, prepare_snapshot_frame, resolve_path
    
    source = camera.stream_source
    capture_source = normalize_capture_source(source)
    
    if capture_source is None:
        return StreamingResponse(
            io.BytesIO(build_placeholder_frame("Chưa cấu hình nguồn camera.", camera.name)),
            media_type="image/jpeg",
        )

    # Nếu là file, thử resolve đường dẫn
    is_file = False
    if isinstance(capture_source, str) and not capture_source.startswith(("rtsp://", "http://", "https://")):
        # Nếu đã là đường dẫn tuyệt đối và tồn tại
        if os.path.isabs(capture_source) and os.path.exists(capture_source):
            is_file = True
        else:
            # Thử tìm trong data/
            data_path = PROJECT_ROOT / "data" / capture_source
            if data_path.exists():
                capture_source = str(data_path.resolve())
                is_file = True
            else:
                # Thử resolve bình thường
                p = resolve_path(capture_source)
                if p.exists():
                    capture_source = str(p)
                    is_file = True

    frame = None
    success = False
    
    # Thử bằng OpenCV trước
    capture = cv2.VideoCapture(capture_source)
    try:
        success, frame = capture.read()
    finally:
        capture.release()

    # Nếu OpenCV thất bại và là file, thử bằng FFmpeg (tốt hơn với H.265)
    if (not success or frame is None) and is_file:
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            out_jpg = tmp.name
        try:
            # Lấy frame tại giây thứ 1
            subprocess.run([
                "ffmpeg", "-y", "-i", capture_source,
                "-ss", "00:00:01", "-frames:v", "1", "-q:v", "2", out_jpg
            ], capture_output=True, timeout=10)
            
            if os.path.exists(out_jpg):
                frame = cv2.imread(out_jpg)
                if frame is not None:
                    success = True
        finally:
            if os.path.exists(out_jpg):
                try: os.remove(out_jpg)
                except: pass

    if not success or frame is None:
        detail = source or "Nguồn camera trống."
        return StreamingResponse(
            io.BytesIO(build_placeholder_frame("Không đọc được hình ảnh camera.", detail)),
            media_type="image/jpeg",
        )

    # Nếu raw=True, trả về ảnh gốc (không vẽ ROI lên trên) -> Dùng cho việc vẽ lại ROI
    if raw:
        return StreamingResponse(io.BytesIO(encode_jpeg(frame)), media_type="image/jpeg")

    prepared = prepare_snapshot_frame(frame, camera.to_dict())
    return StreamingResponse(io.BytesIO(encode_jpeg(prepared)), media_type="image/jpeg")

@camera_router.post("/api/cameras/test-frame")
async def api_test_camera_frame(source: str = Body(..., embed=True), user=Depends(login_required)):
    # Xử lý source
    stream_url = source.strip()
    if not stream_url:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Nguồn phát không được để trống."})
        
    # Hỗ trợ device ID (webcam)
    if stream_url.isdigit():
        stream_url = int(stream_url)
    else:
        # Nếu là file cục bộ, resolve path
        if not (stream_url.startswith("rtsp://") or stream_url.startswith("http://") or stream_url.startswith("https://")):
            p = resolve_path(stream_url)
            if not p.exists():
                 return JSONResponse(status_code=400, content={"ok": False, "error": f"Không tìm thấy file: {stream_url}"})
            stream_url = str(p)

    try:
        cap = cv2.VideoCapture(stream_url)
        if not cap.isOpened():
            return JSONResponse(status_code=400, content={"ok": False, "error": "Không thể kết nối đến nguồn phát."})
        
        # Thử lấy frame (thử tối đa 10 frame cho RTSP)
        success = False
        frame = None
        for _ in range(10):
            success, frame = cap.read()
            if success and frame is not None:
                break
        
        cap.release()
        
        if not success or frame is None:
            return JSONResponse(status_code=400, content={"ok": False, "error": "Kết nối thành công nhưng không lấy được hình ảnh."})
        
        # Encode sang JPG
        _, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        jpg_as_text = base64.b64encode(buffer).decode("utf-8")
        
        return {
            "ok": True, 
            "frame": f"data:image/jpeg;base64,{jpg_as_text}",
            "width": frame.shape[1],
            "height": frame.shape[0]
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": f"Lỗi trích xuất: {str(e)}"})
