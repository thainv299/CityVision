import io
import uuid
import json
from typing import Any, Dict, Optional, List

from fastapi import APIRouter, Request, Depends, Form, File, UploadFile, status, HTTPException, Body
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse, FileResponse
from pathlib import Path

from core.config import ALLOWED_VIDEO_EXTENSIONS, DEFAULT_MODEL_PATH, SAMPLES_DIR
from core.errors import AppError, NotFoundError, ValidationError
from core.utils import build_placeholder_frame, resolve_path
from presentation.container import container, templates
from presentation.middlewares.auth import login_required
from core.config import PROJECT_ROOT  # Đã có trong test_video_views qua resolve_path hoặc config

test_video_router = APIRouter()


def _parse_polygon(value: Any) -> Optional[list]:
    if value in (None, "", []):
        return None
    if isinstance(value, str):
        try:
            data = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValidationError("Polygon JSON không hợp lệ.") from exc
    # Hỗ trợ cả định dạng list trực tiếp và định dạng object {units: "...", points: [...]}
    is_pixels = False
    metadata = {}
    if isinstance(data, dict):
        if "units" in data:
            metadata["units"] = data["units"]
        if "ref_width" in data:
            metadata["ref_width"] = data["ref_width"]
        if "ref_height" in data:
            metadata["ref_height"] = data["ref_height"]
            
        if "points" in data:
            data = data["points"]
        
    if not isinstance(data, list):
        raise ValidationError("Polygon phải là một mảng điểm.")
    
    points = []
    for point in data:
        points.append([float(point[0]), float(point[1])])
        
    if points and len(points) < 3:
        raise ValidationError("Polygon cần tối thiểu 3 điểm.")
        
    return points or None, metadata


def _build_test_settings(form_data: Dict[str, Any], camera: Any) -> Dict[str, Any]:
    model_path_text = str(form_data.get("model_path", "")).strip()
    
    if not model_path_text and camera and camera.model_path:
        model_path_text = camera.model_path
        
    if not model_path_text:
        model_path_text = str(DEFAULT_MODEL_PATH)

    model_path = resolve_path(model_path_text)
    if not model_path.exists():
        raise ValidationError(f"Không tìm thấy model: {model_path}")

    roi_value = form_data.get("roi_points")
    parking_value = form_data.get("no_parking_points")

    if roi_value not in (None, ""):
        roi_points, roi_meta = _parse_polygon(roi_value)
    else:
        roi_points, roi_meta = (None, {})

    if parking_value not in (None, ""):
        no_parking_points, no_park_meta = _parse_polygon(parking_value)
    else:
        no_parking_points, no_park_meta = (None, {})

    if camera is not None:
        if roi_points is None:
            roi_points = camera.roi_points
            roi_meta = camera.roi_meta or {}
        if no_parking_points is None:
            no_parking_points = camera.no_parking_points
            no_park_meta = camera.no_park_meta or {}

    enable_congestion = True
    if "enable_congestion" in form_data:
        val = form_data.get("enable_congestion")
        enable_congestion = str(val).lower() in {"1", "true", "yes", "on"}
    elif camera:
        enable_congestion = camera.enable_congestion

    enable_illegal_parking = True
    if "enable_illegal_parking" in form_data:
        val = form_data.get("enable_illegal_parking")
        enable_illegal_parking = str(val).lower() in {"1", "true", "yes", "on"}
    elif camera:
        enable_illegal_parking = camera.enable_illegal_parking

    enable_license_plate = True
    if "enable_license_plate" in form_data:
        val = form_data.get("enable_license_plate")
        enable_license_plate = str(val).lower() in {"1", "true", "yes", "on"}
    elif camera:
        enable_license_plate = camera.enable_license_plate

    def _parse_float(val, d):
        try:
            return float(val) if val not in (None, "") else d
        except: return d
    def _parse_int(val, d):
        try:
            return int(val) if val not in (None, "") else d
        except: return d

    from database.sqlite_db import get_system_settings
    sys_settings = get_system_settings()

    return {
        "model_path": str(model_path),
        "confidence_threshold": _parse_float(form_data.get("confidence_threshold"), sys_settings.get("confidence", 0.32)),
        "enable_congestion": enable_congestion,
        "enable_illegal_parking": enable_illegal_parking,
        "enable_license_plate": enable_license_plate,
        "stop_seconds": _parse_float(form_data.get("stop_seconds"), 30.0),
        "parking_move_threshold_px": _parse_float(form_data.get("parking_move_threshold_px"), 10.0),
        "process_every_n_frames": _parse_int(form_data.get("process_every_n_frames"), sys_settings.get("frame_skip", 2)),
        "roi_points": roi_points,
        "roi_meta": roi_meta,
        "no_parking_points": no_parking_points,
        "no_park_meta": no_park_meta,
    }


@test_video_router.get("/test-video", name="test_video.test_video_page")
async def test_video_page(request: Request, user=Depends(login_required)):
    if isinstance(user, RedirectResponse):
        return user
    
    # Quét danh sách model khả dụng trong thư mục models/
    models_dir = PROJECT_ROOT / "models"
    available_models = []
    if models_dir.exists():
        for f in models_dir.iterdir():
            if f.is_file() and f.suffix.lower() in [".pt", ".engine"]:
                available_models.append(f.name)
    
    # Sắp xếp để bản test.pt hoặc yolo... lên đầu nếu cần, hoặc alphabet
    available_models.sort()
    
    cameras = container.camera_use_cases.list_cameras()
    return container.render_template(
        request,
        "test_video.html",
        {
            "page": "test-video",
            "cameras": [c.to_dict() for c in cameras],
            "default_model_path": str(DEFAULT_MODEL_PATH),
            "available_models": available_models,
        }
    )



@test_video_router.post("/api/upload-chunk")
async def api_upload_chunk(
    upload_id: str = Form(...),
    chunk_index: int = Form(...),
    total_chunks: int = Form(...),
    file_data: UploadFile = File(...)
):
    import tempfile
    temp_dir = Path(tempfile.gettempdir()) / "video_uploads"
    temp_dir.mkdir(parents=True, exist_ok=True)
    file_path = temp_dir / f"{upload_id}.part"
    
    mode = "ab" if chunk_index > 0 else "wb"
    with open(file_path, mode) as f:
        content = await file_data.read()
        f.write(content)
        
    return JSONResponse(status_code=200, content={"ok": True})
    
@test_video_router.get("/api/server-videos")
async def api_list_server_videos(user=Depends(login_required)):
    data_root = PROJECT_ROOT / "data"
    if not data_root.exists():
        data_root.mkdir(parents=True, exist_ok=True)
        
    grouped_videos = {} # { "Folder Name": [video_info, ...] }
    
    # Duyệt qua các thư mục con trong data/
    for p in data_root.rglob("*"):
        if p.is_file() and p.suffix.lower() in ALLOWED_VIDEO_EXTENSIONS:
            try:
                # Lấy tên thư mục cha (tương đối so với data/)
                rel_parent = p.parent.relative_to(data_root)
                folder_name = str(rel_parent) if str(rel_parent) != "." else "Gốc (samples)"
                
                if folder_name not in grouped_videos:
                    grouped_videos[folder_name] = []
                
                stats = p.stat()
                grouped_videos[folder_name].append({
                    "filename": p.name,
                    "size": stats.st_size,
                    "path": str(p),
                    "rel_path": str(p.relative_to(data_root))
                })
            except Exception:
                continue
    
    # Sắp xếp các video trong từng nhóm
    for folder in grouped_videos:
        grouped_videos[folder].sort(key=lambda x: x["filename"])
        
    return {"ok": True, "groups": grouped_videos}

@test_video_router.get("/api/server-videos/preview")
async def api_get_server_video_preview(path: Optional[str] = None, rel_path: Optional[str] = None, user=Depends(login_required)):
    final_path = path or rel_path
    if not final_path:
        raise HTTPException(status_code=422, detail="Thiếu tham số đường dẫn (path hoặc rel_path).")

    import subprocess, tempfile, os
    
    data_root = PROJECT_ROOT / "data"
    
    # Thử resolve path
    input_path = Path(final_path)
    if not input_path.is_absolute():
        input_path = data_root / final_path
    
    input_path = input_path.resolve()
    
    # Bảo mật: Đảm bảo path nằm trong PROJECT_ROOT
    if not str(input_path).startswith(str(PROJECT_ROOT.resolve())):
        raise HTTPException(status_code=403, detail="Truy cập bị từ chối.")
        
    if not input_path.exists():
        raise HTTPException(status_code=404, detail="Không tìm thấy file video.")
        
    # Tạo file tạm cho ảnh preview
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        out_jpg = tmp.name

    try:
        # Trích xuất frame tại giây thứ 1 (hoặc đầu tiên nếu ngắn hơn)
        # -ss 1 đặt trước -i để nhanh hơn, nhưng đôi khi không chính xác với một số codec
        # Đặt sau -i để chắc chắn lấy được ảnh có nội dung
        process = subprocess.run([
            "ffmpeg", "-y",
            "-i", str(input_path),
            "-ss", "00:00:01",
            "-frames:v", "1",
            "-q:v", "4",  # Chất lượng vừa phải cho preview
            "-f", "image2",
            out_jpg
        ], capture_output=True, timeout=10)
        
        if process.returncode != 0 or not os.path.exists(out_jpg):
            # Nếu giây thứ 1 lỗi (video ngắn), thử lấy frame đầu tiên
            subprocess.run([
                "ffmpeg", "-y",
                "-i", str(input_path),
                "-frames:v", "1",
                "-q:v", "4",
                out_jpg
            ], capture_output=True, timeout=5)

        if os.path.exists(out_jpg):
            with open(out_jpg, "rb") as f:
                content = f.read()
            return StreamingResponse(io.BytesIO(content), media_type="image/jpeg")
        else:
            # Fallback nếu hoàn toàn không trích xuất được
            return StreamingResponse(
                io.BytesIO(build_placeholder_frame("Không có preview", str(final_path))),
                media_type="image/jpeg"
            )
    finally:
        if os.path.exists(out_jpg):
            try: os.remove(out_jpg)
            except: pass

@test_video_router.post("/api/test-jobs")
async def api_create_test_job(
    request: Request,
    user=Depends(login_required),
    camera_id: Optional[str] = Form(None),
    video_file: Optional[UploadFile] = File(None),
    upload_id: Optional[str] = Form(None),
    original_filename: Optional[str] = Form(None),
    model_path: Optional[str] = Form(None),
    confidence_threshold: Optional[str] = Form(None),
    enable_congestion: Optional[str] = Form(None),
    enable_illegal_parking: Optional[str] = Form(None),
    enable_license_plate: Optional[str] = Form(None),
    stop_seconds: Optional[str] = Form(None),
    parking_move_threshold_px: Optional[str] = Form(None),
    process_every_n_frames: Optional[str] = Form(None),
    roi_points: Optional[str] = Form(None),
    no_parking_points: Optional[str] = Form(None),
    server_filename: Optional[str] = Form(None),
):
    job_id = uuid.uuid4().hex
    camera = None
    camera_id_strip = (camera_id or "").strip()
    if not camera_id_strip:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Vui lòng chọn một camera để giám sát."})

    try:
        camera = container.camera_use_cases.get_camera(int(camera_id_strip))
    except NotFoundError:
        return JSONResponse(status_code=404, content={"ok": False, "error": "Camera được chọn không tồn tại."})

    input_stream = None
    input_path = None
    input_ext = ""
    
    # ── XỬ LÝ NGUỒN ĐẦU VÀO ──
    if upload_id:
        # Chế độ Giả lập (Chunked upload)
        import tempfile
        import os
        temp_dir = Path(tempfile.gettempdir()) / "video_uploads"
        file_path = temp_dir / f"{upload_id}.part"
        if not file_path.exists():
            return JSONResponse(status_code=400, content={"ok": False, "error": "Không tìm thấy dữ liệu upload."})
        input_path = str(file_path)
        filename = original_filename or "video.mp4"
        input_ext = Path(filename).suffix.lower()
    elif video_file and video_file.filename:
        # Chế độ Giả lập (Direct upload)
        if not container.file_storage.is_allowed_video(video_file.filename):
            return JSONResponse(status_code=400, content={"ok": False, "error": "Định dạng video không được hỗ trợ."})
        input_ext = Path(video_file.filename).suffix.lower()
        input_stream = io.BytesIO(await video_file.read())
    elif server_filename:
        # Chế độ lấy file từ Server
        file_path = SAMPLES_DIR / server_filename
        if not file_path.exists():
            return JSONResponse(status_code=400, content={"ok": False, "error": f"Không tìm thấy file trên server: {server_filename}"})
        input_path = str(file_path)
        input_ext = file_path.suffix.lower()
    else:
        # Chế độ Giám sát Trực tiếp (Dùng stream_source từ DB)
        if not camera.stream_source:
            return JSONResponse(status_code=400, content={"ok": False, "error": "Camera này chưa cấu hình nguồn phát."})
        input_path = camera.stream_source
        # Thử đoán extension nếu là file, hoặc mặc định .mp4 cho RTSP
        if "." in input_path.split("/")[-1]:
            input_ext = "." + input_path.split(".")[-1].split("?")[0]
        else:
            input_ext = ".mp4"

    form_dict = {
        "model_path": model_path,
        "confidence_threshold": confidence_threshold,
        "enable_congestion": enable_congestion,
        "enable_illegal_parking": enable_illegal_parking,
        "enable_license_plate": enable_license_plate,
        "stop_seconds": stop_seconds,
        "parking_move_threshold_px": parking_move_threshold_px,
        "process_every_n_frames": process_every_n_frames,
        "roi_points": roi_points,
        "no_parking_points": no_parking_points,
    }

    try:
        test_settings = _build_test_settings(form_dict, camera)
        # Bổ sung camera_id vào settings để bridge biết ghi vào đâu
        test_settings["camera_id"] = camera.id
    except ValidationError as exc:
        return JSONResponse(status_code=400, content={"ok": False, "error": exc.message})

    job = container.job_use_cases.submit_job(
        job_id=job_id,
        input_stream=input_stream,
        input_path=input_path,
        input_ext=input_ext,
        settings=test_settings,
        delete_after_job=(upload_id is not None or (video_file is not None and video_file.filename))
    )

    payload = job.to_dict()
    payload["stream_url"] = str(request.url_for("test_video.serve_test_job_stream", job_id=job.id))
    payload["queue_position"] = container.job_use_cases.get_queue_position(job.id)

    return JSONResponse(status_code=202, content={"ok": True, "job": payload})


@test_video_router.get("/api/test-jobs/{job_id}")
async def api_get_test_job(request: Request, job_id: str, user=Depends(login_required)):
    job = container.job_use_cases.get_job(job_id)
    if job is None:
        return JSONResponse(status_code=404, content={"ok": False, "error": "Không tìm thấy job kiểm tra."})
        
    payload = job.to_dict()
    payload["stream_url"] = str(request.url_for("test_video.serve_test_job_stream", job_id=job.id))
    payload["queue_position"] = container.job_use_cases.get_queue_position(job.id)

    return {"ok": True, "job": payload}
@test_video_router.post("/api/test-jobs/{job_id}/pause")
async def api_pause_test_job(job_id: str, user=Depends(login_required)):
    success = container.job_use_cases.pause_job(job_id)
    if not success:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Không thể tạm dừng job này."})
    return {"ok": True, "message": "Đã tạm dừng quá trình phân tích."}


@test_video_router.post("/api/test-jobs/{job_id}/stop")
async def api_stop_test_job(job_id: str, user=Depends(login_required)):
    success = container.job_use_cases.stop_job(job_id)
    if not success:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Không thể dừng job này (có thể đã kết thúc hoặc không tồn tại)."})
    return {"ok": True, "message": "Đã dừng quá trình phân tích."}


@test_video_router.post("/api/test-jobs/{job_id}/resume")
async def api_resume_test_job(job_id: str, user=Depends(login_required)):
    success = container.job_use_cases.resume_job(job_id)
    if not success:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Không thể tiếp tục job này."})
    return {"ok": True, "message": "Đã tiếp tục quá trình phân tích."}
    
@test_video_router.post("/api/test-jobs/{job_id}/quality")
async def api_update_job_quality(job_id: str, quality: str = Body(..., embed=True), user=Depends(login_required)):
    if quality not in ["low", "medium", "high", "ultra"]:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Chất lượng không hợp lệ."})
        
    success = container.job_use_cases.update_job_quality(job_id, quality)
    if not success:
        return JSONResponse(status_code=404, content={"ok": False, "error": "Không tìm thấy job."})
    return {"ok": True, "message": f"Đã yêu cầu đổi sang chất lượng: {quality}"}


@test_video_router.get("/api/test-jobs/{job_id}/stream", name="test_video.serve_test_job_stream")
async def serve_test_job_stream(job_id: str, user=Depends(login_required)):
    return StreamingResponse(
        container.job_use_cases.stream_job_frames(job_id), 
        media_type="multipart/x-mixed-replace; boundary=frame"
    )


@test_video_router.post("/api/test-video/extract-frame")
async def api_extract_video_frame(
    video_file: Optional[UploadFile] = File(None), 
    server_filename: Optional[str] = Form(None),
    user=Depends(login_required)
):
    import tempfile, os, base64, subprocess
    import numpy as np
    import cv2

    tmp_path = None
    input_path = None
    
    if server_filename:
        input_path = SAMPLES_DIR / server_filename
        if not input_path.exists():
             return JSONResponse(status_code=400, content={"ok": False, "error": "Không tìm thấy file trên server."})
        input_path = str(input_path)
    elif video_file and video_file.filename:
        suffix = Path(video_file.filename).suffix.lower()
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            content = await video_file.read()
            tmp.write(content)
            tmp_path = tmp.name
        input_path = tmp_path
    else:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Yêu cầu video_file hoặc server_filename."})

    out_jpg = (tmp_path or str(SAMPLES_DIR / "temp_extract")) + f"_{uuid.uuid4().hex}.jpg"
    try:
        # Dùng ffmpeg CLI thay vì OpenCV — xử lý H.265 ổn định hơn
        result = subprocess.run([
            "ffmpeg", "-y",
            "-threads", "1",          # Force single-thread tránh async_lock
            "-i", input_path,
            "-frames:v", "1",
            "-q:v", "2",
            out_jpg
        ], capture_output=True, timeout=30)

        if result.returncode != 0 or not Path(out_jpg).exists():
            return JSONResponse(status_code=400, content={
                "ok": False,
                "error": "Không thể trích xuất frame. Kiểm tra ffmpeg đã cài chưa."
            })

        with open(out_jpg, "rb") as f:
            jpg_bytes = f.read()

        # Đọc kích thước bằng OpenCV (chỉ đọc JPEG tĩnh — không lỗi)
        arr = np.frombuffer(jpg_bytes, dtype=np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        h, w = frame.shape[:2] if frame is not None else (0, 0)

        return {
            "ok": True,
            "frame_data": f"data:image/jpeg;base64,{base64.b64encode(jpg_bytes).decode()}",
            "width": w,
            "height": h,
        }
    finally:
        for p in (tmp_path, out_jpg):
            try:
                if os.path.exists(p): os.remove(p)
            except: pass

@test_video_router.get("/job-previews/{job_id}.jpg", name="test_video.serve_test_job_preview")
async def serve_test_job_preview(job_id: str, user=Depends(login_required)):
    if isinstance(user, RedirectResponse):
        return user
    job = container.job_use_cases.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404)

    preview_path = container.file_storage.get_preview_path(job_id)
    if preview_path.exists():
        return FileResponse(
            preview_path, 
            media_type="image/jpeg",
            headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"}
        )

    if job.status == "running":
        title = "Đang phân tích video"
    elif job.status == "completed":
        title = "Đã hoàn tất xử lý"
    elif job.status == "failed":
        title = "Không tạo được preview"
    else:
        title = "Đang chờ đến lượt xử lý"

    detail = str(job.message)
    return StreamingResponse(
        io.BytesIO(build_placeholder_frame(title, detail)), 
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"}
    )



