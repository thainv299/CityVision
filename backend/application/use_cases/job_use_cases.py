import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Optional

from application.interfaces.detection_interface import DetectionInterface
from domain.entities.job import Job
from infrastructure.file_system.local_storage import LocalStorage
from database.sqlite_db import log_detected_license_plate


class JobUseCases:
    def __init__(self, detection_service: DetectionInterface, file_storage: LocalStorage):
        self.detection_service = detection_service
        self.file_storage = file_storage
        
        self.executor = ThreadPoolExecutor(max_workers=10)
        self.job_lock = threading.Lock()
        self.jobs: Dict[str, Job] = {}
        self.pause_events: Dict[str, threading.Event] = {}

    def set_job(self, job_id: str, **updates: Any) -> Job:
        with self.job_lock:
            if job_id not in self.jobs:
                self.jobs[job_id] = Job(id=job_id)
            job = self.jobs[job_id]
            for key, value in updates.items():
                if hasattr(job, key):
                    setattr(job, key, value)
            return job

    def stop_camera_jobs(self, camera_id: int):
        """Dừng tất cả các job (nền hoặc test) liên quan đến camera_id này"""
        with self.job_lock:
            to_stop = []
            target_id_str = str(camera_id)
            for job_id, job in self.jobs.items():
                # So sánh string để tránh lệch kiểu dữ liệu int/str
                if str(job.camera_id) == target_id_str and job.status in {"queued", "running"}:
                    to_stop.append(job_id)
            
            for jid in to_stop:
                job = self.jobs[jid]
                job.status = "aborted"
                job.message = "Hệ thống đã dừng tác vụ AI cho camera này."
                if jid in self.pause_events:
                    self.pause_events[jid].clear()
                print(f"[System] Đã dừng job {jid} cho camera {camera_id} thành công.")

    def get_job(self, job_id: str) -> Optional[Job]:
        with self.job_lock:
            return self.jobs.get(job_id)

    def update_job_quality(self, job_id: str, quality: str) -> bool:
        """Cập nhật chất lượng video đang xử lý"""
        with self.job_lock:
            job = self.jobs.get(job_id)
            if not job:
                return False
            # Lưu chất lượng vào progress để Bridge có thể đọc được
            if not job.progress:
                job.progress = {}
            job.progress["requested_quality"] = quality
            return True

    def update_camera_job_settings(self, camera_id: int, settings: Dict[str, Any]):
        """Cập nhật cấu hình tính năng AI cho job của camera đang chạy"""
        with self.job_lock:
            for job_id, job in self.jobs.items():
                if str(job.camera_id) == str(camera_id) and job.status in {"queued", "running"}:
                    if not job.progress:
                        job.progress = {}
                    # Lưu tất cả các cài đặt (bao gồm cấu hình AI và cấu hình hiển thị) vào progress
                    req_s = job.progress.get("requested_settings") or {}
                    for k, v in settings.items():
                        req_s[k] = v
                    job.progress["requested_settings"] = req_s
                    print(f"[System] Đã cập nhật cấu hình cho job {job_id} trong RAM")

    def get_queue_position(self, job_id: str) -> Optional[int]:
        with self.job_lock:
            active_jobs = [
                j for j in self.jobs.values()
                if j.status in {"queued", "running"}
            ]
            active_jobs.sort(key=lambda item: item.submitted_at or 0.0)

        for index, item in enumerate(active_jobs, start=1):
            if item.id == job_id and item.status == "queued":
                return index
        return None

    def pause_job(self, job_id: str) -> bool:
        with self.job_lock:
            job = self.jobs.get(job_id)
            if job and job.status == "running":
                job.is_paused = True
                job.message = "Đang tạm dừng quá trình phân tích..."
                if job_id not in self.pause_events:
                    self.pause_events[job_id] = threading.Event()
                self.pause_events[job_id].set() # Signal pause
                return True
        return False

    def stop_job(self, job_id: str) -> bool:
        with self.job_lock:
            job = self.jobs.get(job_id)
            if job and job.status in {"queued", "running"}:
                job.status = "aborted"
                job.message = "Đã dừng quá trình phân tích bởi người dùng."
                # Xóa sự kiện tạm dừng nếu có để tránh thread bị kẹt khi ngủ
                if job_id in self.pause_events:
                    self.pause_events[job_id].clear() 
                return True
        return False

    def resume_job(self, job_id: str) -> bool:
        with self.job_lock:
            job = self.jobs.get(job_id)
            if job and job.status == "running":
                job.is_paused = False
                job.message = "Đang tiếp tục phân tích..."
                if job_id in self.pause_events:
                    self.pause_events[job_id].clear() # Signal resume
                return True
        return False

    def run_detection_job(
        self,
        job_id: str,
        input_stream: Any,
        input_path: Optional[str],
        input_ext: str,
        detection_settings: Dict[str, Any],
        delete_after_job: bool = False
    ) -> None:
        def handle_progress(progress: Dict[str, Any]) -> None:
            # Lấy job một lần duy nhất với lock
            with self.job_lock:
                job = self.jobs.get(job_id)
                if not job:
                    return None
                if job.status == "aborted":
                    raise RuntimeError("Job da bi huy boi luong stream.")
                
                req_q = job.progress.get("requested_quality") if job.progress else None
                req_settings = job.progress.get("requested_settings") if job.progress else None

            progress_payload = dict(progress)
            preview_jpeg = progress_payload.pop("preview_jpeg", None)
            if preview_jpeg:
                job.latest_frame = preview_jpeg  # Gán atomic không cần lock

            phase = progress.get("phase")
            processed_frames = progress.get("processed_frames")

            if phase == "loading_model":
                message = "Đang tải model YOLO..."
            elif phase == "finalizing_output":
                message = "Đang hoàn tất video kết quả..."
            elif processed_frames is not None:
                message = "Hệ thống đang hoạt động..."
            else:
                message = "Đang xử lý video..."

            # Cập nhật thông số trực tiếp không cần acquire lock liên tục (atomic / GIL-safe)
            job.status = "running"
            job.message = message
            job.error = None
            if job.started_at is None:
                job.started_at = time.time()
            job.progress = progress_payload
            
            # Trả về lệnh đổi chất lượng hoặc đổi cài đặt cho Bridge nếu có
            actions = {}
            if req_q or req_settings:
                with self.job_lock:
                    if req_q and job.progress:
                        job.progress.pop("requested_quality", None)
                    if req_settings and job.progress:
                        job.progress.pop("requested_settings", None)
                if req_q:
                    actions["new_quality"] = req_q
                if req_settings:
                    actions["new_settings"] = req_settings

            return actions if actions else None

        self.set_job(
            job_id,
            status="running",
            message="Đang khởi tạo job detect...",
            error=None,
            started_at=time.time(),
            progress={
                "phase": "starting",
                "processed_frames": 0,
                "source_total_frames": None,
                "progress_percent": 0.0,
                "elapsed_seconds": 0.0,
                "latest_status": "Đang khởi tạo job detect...",
            },
        )

        try:
            summary = self.detection_service.process_video(
                input_stream=input_stream,
                input_path=input_path,
                input_ext=input_ext,
                settings=detection_settings,
                progress_callback=handle_progress,
                pause_event=self.pause_events.get(job_id)
            )
            
            # Lưu biển số được phát hiện vào database
            detected_plates = summary.get("detected_plates", {})
            for plate_text, plate_data in detected_plates.items():
                log_detected_license_plate(
                    license_plate=plate_text,
                    detection_count=plate_data.get("count", 1),
                    avg_confidence=plate_data.get("avg_confidence", 0.0),
                    image_paths=plate_data.get("image_path"),
                )
            
            self.set_job(
                job_id,
                status="completed",
                message="Đã hoàn thành xử lý video.",
                error=None,
                summary=summary,
                output_filename=None,
                finished_at=time.time(),
                progress={
                    "phase": "completed",
                    "processed_frames": summary.get("processed_frames"),
                    "source_total_frames": summary.get("source_total_frames"),
                    "progress_percent": 100.0,
                    "elapsed_seconds": summary.get("processing_seconds"),
                    "latest_status": summary.get("latest_status"),
                },
            )
            # Cleanup pause event
            with self.job_lock:
                self.pause_events.pop(job_id, None)
        except Exception as exc:
            # Kiểm tra xem có phải lỗi thiếu file model YOLO không
            is_model_missing = isinstance(exc, FileNotFoundError) and "mô hình YOLO" in str(exc)
            
            error_msg = str(exc)
            if is_model_missing:
                error_msg = f"LỖI HỆ THỐNG: {error_msg}. Camera này đã bị tự động tắt hoạt động để bảo vệ hệ thống."
                
                # Tự động tắt kích hoạt camera trong database
                camera_id = settings.get("camera_id")
                if camera_id:
                    try:
                        from database import camera_repo
                        from domain.entities.camera import Camera
                        deactivated_camera = Camera(id=int(camera_id), is_active=False)
                        camera_repo.update(deactivated_camera)
                        print(f"[System] TỰ ĐỘNG TẮT HOẠT ĐỘNG camera ID {camera_id} thành công do thiếu file mô hình!")
                    except Exception as db_exc:
                        print(f"[System] Không thể tự động tắt kích hoạt camera {camera_id}: {db_exc}")

            self.set_job(
                job_id,
                status="failed",
                message="Xử lý video thất bại." if not is_model_missing else "Lỗi hệ thống: Thiếu mô hình YOLO.",
                error=error_msg,
                summary=None,
                output_filename=None,
                finished_at=time.time(),
            )
            # Cleanup pause event
            with self.job_lock:
                self.pause_events.pop(job_id, None)
        finally:
            if delete_after_job and input_path:
                import os
                try:
                    os.remove(input_path)
                except Exception:
                    pass

    def submit_job(self, job_id: str, input_stream: Any, input_path: Optional[str], input_ext: str, settings: Dict[str, Any], delete_after_job: bool = False) -> Job:
        submitted_at = time.time()
        camera_id = settings.get("camera_id")
        job = self.set_job(
            job_id,
            status="queued",
            message="Đã nhận yêu cầu. Job sẽ được xử lý theo hàng đợi.",
            error=None,
            output_filename=None,
            summary=None,
            source_video=None,
            submitted_at=submitted_at,
            camera_id=camera_id,
            progress={
                "phase": "queued",
                "processed_frames": 0,
                "source_total_frames": None,
                "progress_percent": 0.0,
                "elapsed_seconds": 0.0,
                "latest_status": "Đang chờ đến lượt xử lý...",
            },
        )

        self.executor.submit(
            self.run_detection_job,
            job_id,
            input_stream,
            input_path,
            input_ext,
            settings,
            delete_after_job
        )
        return job

    def stream_job_frames(self, job_id: str):
        import asyncio
        with self.job_lock:
            job = self.jobs.get(job_id)
        if not job:
            return

        try:
            while True:
                # Đọc trực tiếp trạng thái mà không cần lock liên tục (atomic & GIL-safe)
                if job.status in ("completed", "failed", "aborted"):
                    break
                
                frame_bytes = job.latest_frame
                if frame_bytes:
                    yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame_bytes + b"\r\n"
                
                time.sleep(0.03)  # Điều tiết tốc độ khung hình stream MJPEG để tránh quá tải CPU
        finally:
            with self.job_lock:
                if job.status in ("queued", "running"):
                    job.status = "aborted"
                    job.message = "Stream bị ngắt kết nối."
    def start_active_cameras(self, camera_use_cases: Any):
        """Khởi động tất cả các camera đang hoạt động (is_active=True)"""
        try:
            active_cameras = camera_use_cases.list_cameras()
            for cam in active_cameras:
                if cam.is_active:
                    job_id = f"background_{cam.id}"
                    # Kiểm tra xem job đã chạy chưa
                    existing_job = self.get_job(job_id)
                    if existing_job and existing_job.status in {"queued", "running"}:
                        continue
                        
                    print(f"[Startup] Đang khởi động giám sát nền cho camera: {cam.name} (ID: {cam.id})")
                    
                    from database.sqlite_db import get_camera_settings
                    cam_settings = get_camera_settings(cam.id)
                    
                    settings = {
                        "camera_id": cam.id,
                        "roi_points": cam.roi_points,
                        "roi_meta": cam.roi_meta,
                        "no_parking_points": cam.no_parking_points,
                        "no_park_meta": cam.no_park_meta,
                        "enable_congestion": cam.enable_congestion,
                        "enable_illegal_parking": cam.enable_illegal_parking,
                        "enable_license_plate": cam.enable_license_plate,
                        "enable_ai": cam.enable_ai,
                        "model_path": cam.model_path,
                        "confidence_threshold": cam_settings.get("confidence", 0.37),
                        "process_every_n_frames": cam_settings.get("frame_skip", 2),
                        "congestion_threshold": cam_settings.get("congestion_threshold", 35),
                        "stop_seconds": cam_settings.get("parking_violation_time", 30),
                        "save_to_db": True
                    }
                    
                    # Submit job
                    self.submit_job(
                        job_id=job_id,
                        input_stream=None,
                        input_path=cam.stream_source,
                        input_ext=".mp4", # Giả định mặc định hoặc lấy từ path
                        settings=settings
                    )
        except Exception as e:
            print(f"[Startup] Lỗi khởi động camera nền: {e}")

    def stop_all_jobs(self):
        """Dừng tất cả các job đang chạy hoặc đang chờ"""
        print("[System] Đang dừng tất cả các task xử lý camera...")
        with self.job_lock:
            for job_id, job in self.jobs.items():
                if job.status in {"queued", "running"}:
                    job.status = "aborted"
                    job.message = "Đã dừng task do server tắt."
                    if job_id in self.pause_events:
                        self.pause_events[job_id].clear()
        
        # Shutdown executor
        self.executor.shutdown(wait=False)
