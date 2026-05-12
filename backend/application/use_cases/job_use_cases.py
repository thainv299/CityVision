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
            # Kiểm tra lệnh đổi chất lượng NGAY ĐẦU HÀM để tránh bị set_job ghi đè
            req_q = None
            with self.job_lock:
                job = self.jobs.get(job_id)
                if job and job.progress:
                    req_q = job.progress.get("requested_quality")

            # Kiểm tra xem người dùng có đóng tab hay ngắt Stream chưa để dừng xử lý sớm
            with self.job_lock:
                current = self.jobs.get(job_id)
                if current and current.status == "aborted":
                    raise RuntimeError("Job da bi huy boi luong stream.")

            progress_payload = dict(progress)
            preview_jpeg = progress_payload.pop("preview_jpeg", None)
            if preview_jpeg:
                with self.job_lock:
                    job = self.jobs.get(job_id)
                    if job:
                        job.latest_frame = preview_jpeg

            phase = progress.get("phase")
            percent = progress.get("progress_percent")
            processed_frames = progress.get("processed_frames")
            total_frames = progress.get("source_total_frames")

            if phase == "loading_model":
                message = "Đang tải model YOLO..."
            elif phase == "finalizing_output":
                message = "Đang hoàn tất video kết quả..."
            elif processed_frames is not None:
                message = "Hệ thống đang hoạt động..."
            else:
                message = "Đang xử lý video..."

            self.set_job(
                job_id,
                status="running",
                message=message,
                error=None,
                started_at=time.time(),
                progress=progress_payload,
            )
            
            # Trả về lệnh đổi chất lượng cho Bridge nếu có
            if req_q:
                # Xóa flag sau khi đã lấy ra để gửi đi
                with self.job_lock:
                    job = self.jobs.get(job_id)
                    if job and job.progress:
                        job.progress.pop("requested_quality", None)
                return {"new_quality": req_q}
            return None

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
            self.set_job(
                job_id,
                status="failed",
                message="Xử lý video thất bại.",
                error=str(exc),
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
        try:
            while True:
                with self.job_lock:
                    job = self.jobs.get(job_id)
                if not job:
                    break
                
                if job.latest_frame:
                    yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + job.latest_frame + b"\r\n"
                
                if job.status in ("completed", "failed", "aborted"):
                    break
                
                time.sleep(0.03)  # Điều tiết tốc độ khung hình stream MJPEG để tránh quá tải CPU
        finally:
            with self.job_lock:
                job = self.jobs.get(job_id)
                if job and job.status in ("queued", "running"):
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
                    
                    from database.sqlite_db import get_system_settings
                    sys_settings = get_system_settings()
                    
                    settings = {
                        "camera_id": cam.id,
                        "roi_points": cam.roi_points,
                        "roi_meta": cam.roi_meta,
                        "no_parking_points": cam.no_parking_points,
                        "no_park_meta": cam.no_park_meta,
                        "enable_congestion": cam.enable_congestion,
                        "enable_illegal_parking": cam.enable_illegal_parking,
                        "enable_license_plate": cam.enable_license_plate,
                        "model_path": cam.model_path,
                        "confidence_threshold": sys_settings.get("confidence", 0.32),
                        "process_every_n_frames": sys_settings.get("frame_skip", 2)
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
