import threading
import time
import multiprocessing
from typing import Any, Dict, Optional

from application.interfaces.detection_interface import DetectionInterface
from domain.entities.job import Job
from infrastructure.file_system.local_storage import LocalStorage


def _child_process_run(
    job_id: str,
    input_stream_bytes: Optional[bytes],
    input_path: Optional[str],
    input_ext: str,
    detection_settings: Dict[str, Any],
    progress_queue: multiprocessing.Queue,
    pause_event_shared: Any,
    shared_jobs_state: Any
) -> None:
    """Hàm chạy hoàn toàn độc lập trong Tiến trình con (Bypass GIL, CUDA-safe)."""
    import io
    import sys
    from pathlib import Path

    # Đảm bảo import đúng cấu hình và các Service của Backend trong tiến trình con
    PROJECT_ROOT = Path(__file__).resolve().parents[3]
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    
    # Thiết lập PYTHONPATH bên trong backend/
    BACKEND_DIR = PROJECT_ROOT / "backend"
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))

    from infrastructure.ml.detection_bridge import YoloDetectionService
    from database.sqlite_db import log_detected_license_plate

    detection_service = YoloDetectionService()
    input_stream = io.BytesIO(input_stream_bytes) if input_stream_bytes else None

    current_quality = None

    def handle_progress(progress: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        nonlocal current_quality
        
        if progress:
            # Gửi tiến độ qua Queue về Parent Process
            progress_payload = dict(progress)
            
            # Luôn gửi preview_jpeg để hỗ trợ fallback mượt mà và hiển thị trạng thái tức thì
            try:
                progress_queue.put({
                    "job_id": job_id,
                    "type": "progress",
                    "data": progress_payload
                })
            except Exception as e:
                print(f"[Child Process {job_id}] Lỗi gửi queue: {e}")

        # Đọc các cập nhật/lệnh điều khiển từ Parent qua Shared Dict (Chỉ đọc, KHÔNG ghi để tránh race condition)
        actions = {}
        try:
            state = shared_jobs_state.get(job_id)
            if state is None:
                actions["abort"] = True
            else:
                req_q = state.get("requested_quality")
                req_settings = state.get("requested_settings")
                req_force_preview = state.get("requested_force_preview")
                viewer_count = state.get("viewer_count", 0)
                aborted = state.get("aborted", False)

                if aborted:
                    actions["abort"] = True

                # Chỉ gửi chất lượng mới nếu nó khác với chất lượng hiện tại
                if req_q and req_q != current_quality:
                    actions["new_quality"] = req_q
                    current_quality = req_q
                    
                if req_settings:
                    actions["new_settings"] = req_settings
                if req_force_preview:
                    actions["force_preview"] = True

                actions["viewer_count"] = viewer_count
        except Exception as e:
            print(f"[Child Process {job_id}] Lỗi đọc shared state: {e}")

        return actions if actions else None

    try:
        summary = detection_service.process_video(
            input_stream=input_stream,
            input_path=input_path,
            input_ext=input_ext,
            settings=detection_settings,
            progress_callback=handle_progress,
            pause_event=pause_event_shared
        )

        # Lưu danh sách biển số phát hiện được vào DB
        detected_plates = summary.get("detected_plates", {})
        for plate_text, plate_data in detected_plates.items():
            try:
                log_detected_license_plate(
                    license_plate=plate_text,
                    detection_count=plate_data.get("count", 1),
                    avg_confidence=plate_data.get("avg_confidence", 0.0),
                    image_paths=plate_data.get("image_path"),
                )
            except Exception as db_err:
                print(f"[Child Process {job_id}] Lỗi ghi DB biển số: {db_err}")

        # Gửi thông báo hoàn thành về Parent
        progress_queue.put({
            "job_id": job_id,
            "type": "status",
            "status": "completed",
            "message": "Đã hoàn thành xử lý video.",
            "summary": summary
        })

    except Exception as exc:
        print(f"[Child Process {job_id}] Phát sinh lỗi: {exc}")
        progress_queue.put({
            "job_id": job_id,
            "type": "status",
            "status": "failed",
            "message": f"Xử lý thất bại: {exc}",
            "error": str(exc)
        })


class JobUseCases:
    def __init__(self, detection_service: DetectionInterface, file_storage: LocalStorage):
        self.detection_service = detection_service
        self.file_storage = file_storage

        self.job_lock = threading.Lock()
        self.jobs: Dict[str, Job] = {}

        # Khởi tạo ngữ cảnh Multiprocessing an toàn cho cả Windows và Ubuntu
        self.mp_ctx = multiprocessing.get_context("spawn")
        self.progress_queue = self.mp_ctx.Queue()
        self.processes: Dict[str, multiprocessing.Process] = {}

        # Manager dùng để chia sẻ Event và Dict trạng thái giữa các tiến trình con
        self.mp_manager = self.mp_ctx.Manager()
        self.pause_events: Dict[str, Any] = {}
        self.shared_jobs_state = self.mp_manager.dict()

        # Luồng lắng nghe Queue tiến độ nền trong Tiến trình cha
        self.listener_thread = threading.Thread(target=self._queue_listener, daemon=True)
        self.listener_thread.start()

    def _queue_listener(self):
        """Luồng đón dữ liệu tiến độ từ các tiến trình con để cập nhật vào RAM tiến trình chính FastAPI."""
        import queue
        last_check_time = time.time()
        while True:
            # Kiểm tra timeout cho các job tạm thời cứ sau mỗi 3 giây
            now = time.time()
            if now - last_check_time >= 3.0:
                last_check_time = now
                try:
                    self._check_temporary_jobs_timeouts()
                except Exception as e:
                    print(f"[System] Lỗi khi quét timeout: {e}")

            try:
                msg = self.progress_queue.get(timeout=1.0)
                if not msg:
                    continue

                job_id = msg.get("job_id")
                msg_type = msg.get("type")

                with self.job_lock:
                    job = self.jobs.get(job_id)
                    if not job:
                        continue
                    
                    # Nếu Job đã kết thúc (aborted, completed, failed), bỏ qua các gói tiến độ cũ còn sót lại trong Queue
                    if job.status in {"completed", "failed", "aborted"}:
                        continue

                    if msg_type == "progress":
                        data = msg.get("data")
                        preview_jpeg = data.pop("preview_jpeg", None)
                        if preview_jpeg:
                            job.latest_frame = preview_jpeg
                        
                        # Đồng bộ tiến trình
                        job.progress = data
                        if job.started_at is None:
                            job.started_at = time.time()
                            
                        # Đảm bảo cập nhật message trạng thái về UI
                        phase = data.get("phase")
                        processed_frames = data.get("processed_frames")
                        if phase == "loading_model":
                            job.message = "Đang tải model YOLO..."
                        elif phase == "finalizing_output":
                            job.message = "Đang hoàn tất video kết quả..."
                        elif processed_frames is not None:
                            job.message = "Hệ thống đang hoạt động..."
                        else:
                            job.message = "Đang xử lý video..."

                    elif msg_type == "status":
                        status = msg.get("status")
                        job.status = status
                        job.message = msg.get("message")
                        job.error = msg.get("error")
                        job.summary = msg.get("summary")
                        job.finished_at = time.time()

                        # Thu hồi tài nguyên tiến trình con khi kết thúc
                        if status in {"completed", "failed", "aborted"}:
                            self._cleanup_process(job_id)

            except queue.Empty:
                continue
            except Exception as e:
                # Tránh in lỗi lung tung khi server tắt
                pass

    def _check_temporary_jobs_timeouts(self):
        """Tự động dừng các job tạm thời khi frontend ngắt kết nối (không poll nữa)."""
        with self.job_lock:
            now = time.time()
            to_stop = []
            for job_id, job in self.jobs.items():
                if not job_id.startswith("background_") and job.status in {"queued", "running"}:
                    if job.last_polled_at is not None:
                        # Frontend đã từng poll → nếu quá 15 giây không poll lại thì coi như đã đóng trình duyệt
                        if now - job.last_polled_at > 15.0:
                            to_stop.append(job_id)
                    else:
                        # Chưa có poll nào → ân hạn 30 giây kể từ khi submit (thời gian load model)
                        submitted = job.submitted_at or now
                        if now - submitted > 30.0:
                            to_stop.append(job_id)

            for job_id in to_stop:
                job = self.jobs[job_id]
                job.status = "aborted"
                job.message = "Đã dừng tự động do không phát hiện người xem (timeout)."
                print(f"[System] Tự động dừng job tạm thời {job_id} (camera {job.camera_id}) do mất kết nối với client.")
                
                try:
                    state = dict(self.shared_jobs_state.get(job_id) or {})
                    state["aborted"] = True
                    self.shared_jobs_state[job_id] = state
                except Exception:
                    pass
                
                self._cleanup_process(job_id)

    def _cleanup_process(self, job_id: str):
        """Thu hồi tài nguyên của một tiến trình con"""
        p = self.processes.pop(job_id, None)
        if p:
            # Đợi tiến trình con tự kết thúc (đã set aborted=True trước đó)
            try:
                p.join(timeout=1.5)
            except Exception:
                pass

            # Nếu vẫn còn sống mới cưỡng chế dừng (Hard kill)
            if p.is_alive() and p.pid:
                import platform
                import subprocess
                try:
                    if platform.system().lower() == "windows":
                        subprocess.run(["taskkill", "/f", "/t", "/pid", str(p.pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    else:
                        subprocess.run(["kill", "-9", str(p.pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except Exception:
                    pass

            # Giải phóng tài nguyên Python còn lại
            try:
                p.kill()
                p.terminate()
                p.join(timeout=0.5)
            except Exception:
                pass
                    
        self.pause_events.pop(job_id, None)
        try:
            self.shared_jobs_state.pop(job_id, None)
        except Exception:
            pass

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
        try:
            from database.sqlite_db import close_dangling_records_for_camera
            close_dangling_records_for_camera(camera_id)
        except Exception as e:
            print(f"[System] Lỗi khi đóng bản ghi dang dở của camera {camera_id}: {e}")

        with self.job_lock:
            to_stop = []
            target_id_str = str(camera_id)
            for job_id, job in self.jobs.items():
                if str(job.camera_id) == target_id_str and job.status in {"queued", "running"}:
                    to_stop.append(job_id)

            
            for jid in to_stop:
                job = self.jobs[jid]
                job.status = "aborted"
                job.message = "Hệ thống đã dừng tác vụ AI cho camera này."
                
                # Kích hoạt dừng tiến trình vật lý
                self._cleanup_process(jid)
                print(f"[System] Đã dừng tiến trình AI {jid} cho camera {camera_id} thành công.")

    def get_job(self, job_id: str) -> Optional[Job]:
        with self.job_lock:
            job = self.jobs.get(job_id)
            if job:
                job.last_heartbeat = time.time()
            return job

    def update_job_quality(self, job_id: str, quality: str) -> bool:
        """Cập nhật chất lượng video đang xử lý"""
        with self.job_lock:
            job = self.jobs.get(job_id)
            if not job:
                return False
            if not job.progress:
                job.progress = {}
            job.progress["requested_quality"] = quality

            # Cập nhật shared state
            try:
                state = dict(self.shared_jobs_state.get(job_id) or {})
                state["requested_quality"] = quality
                self.shared_jobs_state[job_id] = state
                return True
            except Exception:
                return False

    def update_camera_job_settings(self, camera_id: int, settings: Dict[str, Any]):
        """Cập nhật cấu hình tính năng AI cho job của camera đang chạy"""
        with self.job_lock:
            for job_id, job in self.jobs.items():
                if str(job.camera_id) == str(camera_id) and job.status in {"queued", "running"}:
                    if not job.progress:
                        job.progress = {}
                    req_s = job.progress.get("requested_settings") or {}
                    for k, v in settings.items():
                        req_s[k] = v
                    job.progress["requested_settings"] = req_s

                    # Gửi cập nhật sang Shared Dict để child process đọc
                    try:
                        state = dict(self.shared_jobs_state.get(job_id) or {})
                        req_s_shared = state.get("requested_settings") or {}
                        for k, v in settings.items():
                            req_s_shared[k] = v
                        state["requested_settings"] = req_s_shared
                        self.shared_jobs_state[job_id] = state
                        print(f"[System] Đã cập nhật cấu hình cho tiến trình con {job_id}")
                    except Exception as e:
                        print(f"[System] Lỗi đồng bộ cấu hình tiến trình con: {e}")

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
                
                # Tạo event tạm dừng đồng bộ qua Manager
                if job_id not in self.pause_events:
                    self.pause_events[job_id] = self.mp_manager.Event()
                self.pause_events[job_id].set()
                return True
        return False

    def stop_job(self, job_id: str) -> bool:
        with self.job_lock:
            job = self.jobs.get(job_id)
            if job and job.status in {"queued", "running"}:
                job.status = "aborted"
                job.message = "Đã dừng quá trình phân tích bởi người dùng."
                
                # Đóng các bản ghi dang dở của camera tương ứng
                if job.camera_id:
                    try:
                        from database.sqlite_db import close_dangling_records_for_camera
                        close_dangling_records_for_camera(int(job.camera_id))
                    except Exception as e:
                        print(f"[System] Lỗi khi đóng bản ghi dang dở cho job {job_id}: {e}")

                # Đánh dấu cờ aborted trong shared state để child process dừng chủ động nếu có thể
                try:
                    state = dict(self.shared_jobs_state.get(job_id) or {})
                    state["aborted"] = True
                    self.shared_jobs_state[job_id] = state
                except Exception:
                    pass

                self._cleanup_process(job_id)
                return True
        return False


    def resume_job(self, job_id: str) -> bool:
        with self.job_lock:
            job = self.jobs.get(job_id)
            if job and job.status == "running":
                job.is_paused = False
                job.message = "Đang tiếp tục phân tích..."
                if job_id in self.pause_events:
                    self.pause_events[job_id].clear()
                return True
        return False

    def submit_job(self, job_id: str, input_stream: Any, input_path: Optional[str], input_ext: str, settings: Dict[str, Any], delete_after_job: bool = False) -> Job:
        submitted_at = time.time()
        camera_id = settings.get("camera_id")

        # Đọc dữ liệu BytesIO từ tiến trình chính trước khi chuyển giao (vì BytesIO không thể pickled truyền sang process khác)
        input_stream_bytes = input_stream.getvalue() if input_stream else None

        job = self.set_job(
            job_id,
            status="running",
            message="Đang khởi tạo tiến trình AI độc lập...",
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
                "latest_status": "Đang khởi tạo tiến trình AI...",
            },
        )

        # Tạo Event pause đồng bộ
        if job_id not in self.pause_events:
            self.pause_events[job_id] = self.mp_manager.Event()

        # Đăng ký thông tin ban đầu vào Shared Dict
        try:
            self.shared_jobs_state[job_id] = {
                "requested_quality": None,
                "requested_settings": None,
                "requested_force_preview": False,
                "viewer_count": 0
            }
        except Exception as e:
            print(f"[System] Lỗi khởi tạo shared state: {e}")

        # Khởi chạy Tiến trình con (Process) thay vì Thread
        process = self.mp_ctx.Process(
            target=_child_process_run,
            args=(
                job_id,
                input_stream_bytes,
                input_path,
                input_ext,
                settings,
                self.progress_queue,
                self.pause_events[job_id],
                self.shared_jobs_state
            ),
            daemon=True
        )
        self.processes[job_id] = process
        process.start()

        return job

    def request_single_preview(self, job_id: str):
        with self.job_lock:
            job = self.jobs.get(job_id)
            if job and job.status in ("queued", "running"):
                if not job.progress:
                    job.progress = {}
                job.progress["requested_force_preview"] = True

                # Đánh dấu cờ trong Shared Dict
                try:
                    state = dict(self.shared_jobs_state.get(job_id) or {})
                    state["requested_force_preview"] = True
                    self.shared_jobs_state[job_id] = state
                except Exception:
                    pass

    def stream_job_frames(self, job_id: str):
        with self.job_lock:
            job = self.jobs.get(job_id)
        if not job:
            return

        with self.job_lock:
            job.viewer_count += 1
            # Cập nhật số người xem vào Shared Dict
            try:
                state = dict(self.shared_jobs_state.get(job_id) or {})
                state["viewer_count"] = job.viewer_count
                self.shared_jobs_state[job_id] = state
            except Exception:
                pass

        try:
            while True:
                if job.status in ("completed", "failed", "aborted"):
                    break
                
                # Cập nhật heartbeat khi đang đẩy stream MJPEG
                job.last_heartbeat = time.time()
                
                frame_bytes = job.latest_frame
                if frame_bytes:
                    yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame_bytes + b"\r\n"
                
                time.sleep(0.03)  # Tránh nghẽn băng thông
        finally:
            with self.job_lock:
                if job.status in ("queued", "running"):
                    job.viewer_count = max(0, job.viewer_count - 1)
                    
                    # Đồng bộ lại số người xem về Shared Dict
                    try:
                        state = dict(self.shared_jobs_state.get(job_id) or {})
                        state["viewer_count"] = job.viewer_count
                        self.shared_jobs_state[job_id] = state
                    except Exception:
                        pass

                    if not job_id.startswith("background_") and job.viewer_count == 0:
                        job.status = "aborted"
                        job.message = "Stream bị ngắt kết nối."
                        self._cleanup_process(job_id)

    def start_active_cameras(self, camera_use_cases: Any):
        """Khởi động tất cả các camera đang hoạt động (is_active=True)"""
        try:
            active_cameras = camera_use_cases.list_cameras()
            for cam in active_cameras:
                if cam.is_active:
                    job_id = f"background_{cam.id}"
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
                    
                    self.submit_job(
                        job_id=job_id,
                        input_stream=None,
                        input_path=cam.stream_source,
                        input_ext=".mp4",
                        settings=settings
                    )
        except Exception as e:
            print(f"[Startup] Lỗi khởi động camera nền: {e}")

    def stop_all_jobs(self):
        """Dừng tất cả các job đang chạy hoặc đang chờ"""
        print("[System] Đang dừng tất cả các task xử lý camera...")
        try:
            from database.sqlite_db import close_all_dangling_records
            close_all_dangling_records()
        except Exception as e:
            print(f"[System] Lỗi khi đóng tất cả bản ghi dang dở khi tắt server: {e}")

        with self.job_lock:
            for job_id in list(self.processes.keys()):
                self._cleanup_process(job_id)
            for job_id, job in self.jobs.items():
                if job.status in {"queued", "running"}:
                    job.status = "aborted"
                    job.message = "Đã dừng task do server tắt."

