import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
import numpy as np
import cv2

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from modules.traffic.traffic_monitor import TrafficMonitor
from modules.parking.tracking_stabilizer import StationaryVehicleStabilizer
from modules.ocr.ocr_manager import OCRManager
from unittest.mock import patch
from backend.infrastructure.ml.detection_bridge import (
    _read_exact_into_ndarray,
    _probe_stream_metadata,
    _build_label_lookup,
    _load_model,
    _resolve_rtsp_push_enabled,
    process_video,
)


class TestPerformanceOptimizations(unittest.TestCase):
    """Kiểm thử các tối ưu hiệu năng cho detection_bridge và các module liên quan."""

    def test_01_traffic_monitor_1080p_mask_accuracy_and_buffer_reuse(self):
        """TrafficMonitor tính mask ở độ phân giải 1/4 vẫn chính xác và tái sử dụng buffer."""
        roi = np.array([[0, 0], [1920, 0], [1920, 1080], [0, 1080]], dtype=np.int32)
        tm = TrafficMonitor(roi_polygon=roi, congestion_threshold=35.0)

        # Box 500x500 ~ 250,000 / 2,073,600 = 12.06%
        tm.reset_counters()
        tm.log_vehicle(track_id=1, cx=500, cy=500, current_time=1.0, bbox=(250, 250, 750, 750))
        _, _, _, _ = tm.calculate_speed_and_status(1.0, (1080, 1920, 3))
        occ1 = tm.last_occupancy
        self.assertAlmostEqual(occ1, 12.06, delta=0.8)

        buf_id_first = id(tm._vehicles_mask)
        overlap_id_first = id(tm._overlap_mask)

        # Gọi lần 2 phải tái sử dụng đúng cùng buffer (không cấp phát mới)
        tm.reset_counters()
        tm.log_vehicle(track_id=1, cx=505, cy=500, current_time=1.05, bbox=(255, 250, 755, 750))
        _, _, _, _ = tm.calculate_speed_and_status(1.05, (1080, 1920, 3))
        self.assertEqual(id(tm._vehicles_mask), buf_id_first)
        self.assertEqual(id(tm._overlap_mask), overlap_id_first)

    def test_02_stabilizer_filter_duplicate_indices_np(self):
        """filter_duplicate_indices_np lọc đúng box trùng và giữ lại box xe khác."""
        stab = StationaryVehicleStabilizer()
        xyxy = np.array([
            [100, 100, 300, 300],  # 0: Xe 1 (conf 0.9)
            [105, 102, 305, 302],  # 1: Trùng Xe 1 (conf 0.6) -> bị lọc
            [600, 100, 800, 300],  # 2: Xe 2 ở vị trí khác (conf 0.8) -> giữ lại
        ], dtype=np.int32)
        conf = np.array([0.9, 0.6, 0.8], dtype=np.float32)

        suppressed = stab.filter_duplicate_indices_np(xyxy, conf, [0, 1, 2])
        self.assertEqual(suppressed, {1})

    def test_03_read_exact_into_ndarray_writable(self):
        """_read_exact_into_ndarray đọc đúng số byte vào ndarray writable (không copy thừa)."""
        raw_arr = np.arange(10 * 20 * 3, dtype=np.uint8)
        pipe = io.BytesIO(raw_arr.tobytes())
        out = _read_exact_into_ndarray(pipe, (10, 20, 3), 10 * 20 * 3)
        self.assertIsNotNone(out)
        self.assertEqual(out.shape, (10, 20, 3))
        self.assertTrue(out.flags["WRITEABLE"])
        np.testing.assert_array_equal(out.reshape(-1), raw_arr)

        # EOF thiếu byte phải trả về None
        short_pipe = io.BytesIO(b"\x01\x02\x03")
        self.assertIsNone(_read_exact_into_ndarray(short_pipe, (10, 20, 3), 10 * 20 * 3))

    def test_04_ocr_manager_debug_window_disabled_by_default(self):
        """OCRManager mặc định tắt dựng ảnh debug window khi chạy server."""
        mgr = OCRManager(reader=None)
        self.assertFalse(mgr.enable_debug_window)
        mgr.show_debug_window()
        self.assertTrue(mgr.enable_debug_window)

    def test_05_probe_and_process_video_1080p_e2e(self):
        """Chạy thử pipeline process_video trên video tổng hợp 1080p (giữ nguyên 1080p, stride=2)."""
        model_path = PROJECT_ROOT / "models" / "VTS_FINETUNE.pt"
        if not model_path.exists():
            self.skipTest("Không có models/VTS_FINETUNE.pt để chạy E2E")

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            vid_path = tmp.name

        try:
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            writer = cv2.VideoWriter(vid_path, fourcc, 25.0, (1920, 1080))
            self.assertTrue(writer.isOpened())
            # Vẽ khung hình có họa tiết rõ để vượt qua kiểm tra _is_frame_valid()
            base_frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
            cv2.rectangle(base_frame, (100, 100), (1800, 950), (60, 120, 180), -1)
            cv2.rectangle(base_frame, (300, 300), (600, 500), (200, 200, 200), -1)
            for _ in range(12):
                writer.write(base_frame)
            writer.release()

            w, h, fps_val, total, is_hevc = _probe_stream_metadata(Path(vid_path))
            self.assertEqual((w, h), (1920, 1080))
            self.assertGreater(fps_val, 0)
            self.assertFalse(is_hevc)

            frames_seen = []

            def on_progress(payload):
                pf = payload.get("processed_frames", 0)
                frames_seen.append(pf)
                if pf >= 6:
                    return {"abort": True}
                return {"viewer_count": 0}

            summary = process_video(
                input_path=vid_path,
                settings={
                    "camera_id": 999,
                    "model_path": str(model_path),
                    "enable_ai": True,
                    "enable_congestion": True,
                    "enable_illegal_parking": True,
                    "enable_license_plate": False,
                    "enable_weapon_detection": False,
                    "process_every_n_frames": 2,
                    "save_to_db": False,
                    "telegram_enabled": False,
                },
                progress_callback=on_progress,
            )

            self.assertTrue(any(f >= 6 for f in frames_seen))
            self.assertGreaterEqual(summary["processing_seconds"], 0.0)
        finally:
            if os.path.exists(vid_path):
                try:
                    os.remove(vid_path)
                except Exception:
                    pass

    def test_06_stream_job_frames_no_duplicate_mjpeg_chunks(self):
        """stream_job_frames chỉ đẩy MJPEG khi có frame mới và thức dậy ngay khi có tín hiệu."""
        from backend.application.use_cases.job_use_cases import JobUseCases
        from backend.domain.entities.job import Job

        uc = JobUseCases.__new__(JobUseCases)
        uc.job_lock = threading.RLock()
        uc.jobs = {}
        uc.shared_jobs_state = {}
        uc.processes = {}
        uc.pause_events = {}

        job = Job(id="background_1", status="running", camera_id=1)
        uc.jobs[job.id] = job
        # Job.to_dict vẫn phải serialize JSON sạch (không lọt threading.Condition)
        json.dumps(job.to_dict())

        # Job đã hoàn tất trước khi mở stream -> 0 chunk và không tăng viewer_count
        done_job = Job(id="done_1", status="completed", camera_id=2, latest_frame=b"OLD", frame_seq=1)
        uc.jobs[done_job.id] = done_job
        self.assertEqual(list(uc.stream_job_frames(done_job.id)), [])
        self.assertEqual(done_job.viewer_count, 0)

        def producer():
            time.sleep(0.03)
            with job.frame_condition:
                job.latest_frame = b"JPEG_1"
                job.frame_seq = 1
                job.frame_condition.notify_all()
            # Đợi lâu hơn 0.03s cũ để chứng minh stream không gửi lặp lại frame 1
            time.sleep(0.12)
            with uc.job_lock:
                with job.frame_condition:
                    job.latest_frame = b"JPEG_2"
                    job.frame_seq = 2
                    job.status = "completed"
                    job.frame_condition.notify_all()

        t = threading.Thread(target=producer, daemon=True)
        t.start()
        chunks = list(uc.stream_job_frames(job.id))
        t.join(timeout=2.0)

        self.assertEqual(len(chunks), 2)
        self.assertIn(b"JPEG_1", chunks[0])
        self.assertIn(b"JPEG_2", chunks[1])

    def test_07_load_engine_model_handles_trtexec_and_metadata_headers(self):
        """_load_model gán custom_names chuẩn cho .engine build từ trtexec (không bị dính class0..class998)."""
        class _FakeTrtexecYOLO:
            def __init__(self, model_path, task=None):
                self.model_path = model_path
                self.task = task

            @property
            def names(self):
                return {i: f"class{i}" for i in range(999)}

        with tempfile.TemporaryDirectory() as tmpdir:
            trtexec_engine = Path(tmpdir) / "trtexec.engine"
            trtexec_engine.write_bytes(b"\x00\x01\x02\x03raw_tensorrt_plan_bytes")

            meta_engine = Path(tmpdir) / "ultralytics.engine"
            meta_json = json.dumps({
                "names": {"0": "person", "1": "car", "2": "knife"}
            }).encode("utf-8")
            meta_engine.write_bytes(len(meta_json).to_bytes(4, "little") + meta_json + b"TRT_PLAN")

            from backend.infrastructure.ml.detection_bridge import DETECTABLE_LABELS

            with patch("backend.infrastructure.ml.detection_bridge.YOLO", _FakeTrtexecYOLO):
                m_trt = _load_model(trtexec_engine)
                canon_trt, _ = _build_label_lookup(m_trt)
                self.assertEqual(canon_trt.get(0), "person")
                self.assertEqual(canon_trt.get(2), "car")
                self.assertEqual(canon_trt.get(4), "license_plate")
                self.assertEqual(canon_trt.get(7), "knife")
                self.assertEqual(canon_trt.get(8), "pistol")
                self.assertEqual(canon_trt.get(9), "sword")
                self.assertEqual(canon_trt.get(10), "helmet")
                self.assertEqual(canon_trt.get(11), "mask")
                self.assertEqual(canon_trt.get(12), "backpack")
                for lbl in (
                    "person", "bicycle", "car", "motorcycle", "license_plate",
                    "bus", "truck", "knife", "pistol", "sword", "helmet", "mask", "backpack",
                ):
                    self.assertIn(lbl, DETECTABLE_LABELS)

                m_meta = _load_model(meta_engine)
                canon_meta, _ = _build_label_lookup(m_meta)
                self.assertEqual(canon_meta, {0: "person", 1: "car", 2: "knife"})

    def test_08_probe_stream_metadata_rtsp_na_nb_frames(self):
        """_probe_stream_metadata không bị fallback mất HEVC/1080p khi ffprobe trả nb_frames='N/A' trên RTSP."""
        fake_stdout = json.dumps({
            "streams": [{
                "codec_name": "hevc",
                "width": 1920,
                "height": 1080,
                "r_frame_rate": "25/1",
                "nb_frames": "N/A",
            }]
        })
        fake_res = type("_Res", (), {"stdout": fake_stdout})()
        with patch("backend.infrastructure.ml.detection_bridge.subprocess.run", return_value=fake_res):
            w, h, fps_val, total, is_hevc = _probe_stream_metadata("rtsp://127.0.0.1:554/Streaming/Channels/101")
        self.assertEqual((w, h, total, is_hevc), (1920, 1080, 0, True))
        self.assertAlmostEqual(fps_val, 25.0, places=2)

    def test_09_resolve_rtsp_push_enabled(self):
        """_resolve_rtsp_push_enabled ưu tiên settings, ENABLE_RTSP_PUSH, và wan_transmission_mode trong DB."""
        self.assertFalse(_resolve_rtsp_push_enabled({"enable_rtsp_push": False}))
        self.assertTrue(_resolve_rtsp_push_enabled({"enable_rtsp_push": True}))

        with patch.dict(os.environ, {"ENABLE_RTSP_PUSH": "0"}):
            self.assertFalse(_resolve_rtsp_push_enabled({}))
        with patch.dict(os.environ, {"ENABLE_RTSP_PUSH": "1"}):
            self.assertTrue(_resolve_rtsp_push_enabled({}))

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ENABLE_RTSP_PUSH", None)
            with patch("backend.database.sqlite_db.get_system_setting", side_effect=lambda k, d="": "mjpeg" if k == "wan_transmission_mode" else d):
                self.assertFalse(_resolve_rtsp_push_enabled({}))
            with patch("backend.database.sqlite_db.get_system_setting", side_effect=lambda k, d="": "webrtc" if k == "wan_transmission_mode" else d):
                self.assertTrue(_resolve_rtsp_push_enabled({}))

    def test_10_mjpeg_preview_delivers_fast_frames_without_rtsp_push(self):
        """MJPEG không bị rớt frame < 40ms trên RTSP live và không gọi FFmpeg RTSP push khi tắt."""
        valid_frame = np.full((720, 1280, 3), (60, 120, 180), dtype=np.uint8)
        cv2.rectangle(valid_frame, (100, 100), (400, 400), (220, 220, 220), -1)

        class _FakeStream:
            def __init__(self, path, force_single_thread=False, pre_probed=None):
                self.width, self.height, self.fps, self.total_frames = 1280, 720, 20.0, 0
                self.draw_w, self.draw_h = 1280, 720
                self.preview_w, self.preview_h = 1280, 720
                self._count = 0

            def start(self):
                return self

            def isOpened(self):
                return self._count < 30

            def read(self):
                if self._count >= 30:
                    return False, None
                self._count += 1
                # Mô phỏng RTSP khi frame_skip=2: frame chẵn xử lý lâu (20ms), frame lẻ skip về ngay (3ms)
                time.sleep(0.020 if (self._count % 2 == 1) else 0.003)
                return True, valid_frame.copy()

            def release(self):
                pass

        class _FakeEncoder:
            def encode(self, frame, w, h, q):
                # Thời gian nén JPEG (10ms) lớn hơn khoảng trễ frame skip (3ms):
                # trước kia frame chẵn bị frame lẻ ghi đè mất; nay cả 2 đều phải được đẩy ra ngay.
                time.sleep(0.010)
                return b"JPEG_BYTES"

        previews_received = []

        def on_progress(payload):
            if "preview_jpeg" in payload:
                previews_received.append(payload["preview_jpeg"])
            if payload.get("processed_frames", 0) >= 8:
                return {"abort": True, "viewer_count": 1}
            return {"viewer_count": 1}

        with patch.dict(os.environ, {"ENABLE_RTSP_PUSH": "0"}), \
             patch("backend.infrastructure.ml.detection_bridge._probe_stream_metadata", return_value=(1280, 720, 20.0, 0, False)), \
             patch("backend.infrastructure.ml.detection_bridge.VideoStream", _FakeStream), \
             patch("backend.infrastructure.ml.detection_bridge.HardwareJPEGEncoder", _FakeEncoder), \
             patch("backend.infrastructure.ml.detection_bridge.subprocess.Popen") as popen_mock:
            process_video(
                input_path="rtsp://127.0.0.1:554/live",
                settings={
                    "camera_id": 1,
                    "model_path": "unused.pt",
                    "enable_ai": False,
                    "enable_congestion": False,
                    "enable_illegal_parking": False,
                    "enable_license_plate": False,
                    "enable_weapon_detection": False,
                    "save_to_db": False,
                    "telegram_enabled": False,
                },
                progress_callback=on_progress,
            )

        popen_mock.assert_not_called()
        self.assertGreaterEqual(len(previews_received), 4)


if __name__ == "__main__":
    unittest.main()
