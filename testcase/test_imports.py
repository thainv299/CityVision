import unittest
import sys
from pathlib import Path

# Setup paths
PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

class TestModuleImports(unittest.TestCase):
    """Kiểm tra import tất cả thư viện phụ thuộc và module ứng dụng"""

    def test_01_import_third_party_libraries(self):
        """Kiểm tra import các thư viện bên thứ 3 (OpenCV, NumPy, FastAPI, YOLO, PIL, etc.)"""
        try:
            import cv2
            import numpy as np
            import fastapi
            from PIL import Image, ImageDraw, ImageFont
            import sqlite3
            import werkzeug
            print("-> [OK] Import 3rd-party basic libraries (cv2, numpy, fastapi, PIL, sqlite3, werkzeug)")
        except ImportError as e:
            self.fail(f"Lỗi import thư viện bên thứ 3 cơ bản: {e}")

    def test_02_import_ml_libraries(self):
        """Kiểm tra import các thư viện Machine Learning (Ultralytics YOLO, PaddleOCR)"""
        try:
            from ultralytics import YOLO
            import paddleocr
            print("-> [OK] Import ML libraries (ultralytics, paddleocr)")
        except ImportError as e:
            self.fail(f"Lỗi import thư viện ML: {e}")

    def test_03_import_core_config(self):
        """Kiểm tra import cấu hình hệ thống (core.config)"""
        try:
            import core.config as core_cfg
            import backend.core.config as backend_cfg
            self.assertTrue(hasattr(core_cfg, "DATABASE_PATH"))
            self.assertTrue(hasattr(backend_cfg, "DATABASE_PATH"))
            print("-> [OK] Import core configuration modules (core.config, backend.core.config)")
        except ImportError as e:
            self.fail(f"Lỗi import module cấu hình: {e}")

    def test_04_import_database_modules(self):
        """Kiểm tra import các module CSDL và Repository"""
        try:
            from backend.database.sqlite_db import (
                connect, init_db, log_weapon_detection, get_weapon_detections,
                log_parking_violation, log_congestion, log_detected_license_plate,
                get_system_setting, update_system_setting
            )
            from backend.domain.entities.user import User
            from backend.domain.entities.camera import Camera
            from backend.database.sqlite_user_repo import SqliteUserRepository
            from backend.database.sqlite_camera_repo import SqliteCameraRepository
            print("-> [OK] Import Database modules & Repositories")
        except ImportError as e:
            self.fail(f"Lỗi import module CSDL: {e}")

    def test_05_import_managers(self):
        """Kiểm tra import các Manager nghiệp vụ"""
        try:
            from modules.parking.parking_manager import ParkingManager
            from modules.ocr.ocr_manager import OCRManager
            from modules.traffic.traffic_monitor import TrafficMonitor
            from modules.weapon.weapon_manager import WeaponManager
            from modules.utils.alpr_logger import ALPRLogger
            from modules.utils.traffic_alert_manager import TrafficAlertManager
            from modules.utils.async_io_worker import AsyncIOWorker
            from modules.utils.hw_jpeg_encoder import HardwareJPEGEncoder
            print("-> [OK] Import Business Managers (Weapon, Parking, Traffic, OCR, ALPR, Alert, AsyncIO)")
        except ImportError as e:
            self.fail(f"Lỗi import Business Manager: {e}")

    def test_06_import_detection_bridge(self):
        """Kiểm tra import cầu nối xử lý luồng camera AI (detection_bridge)"""
        try:
            from backend.infrastructure.ml.detection_bridge import process_video, VideoStream, _display_label, _canonical_label
            print("-> [OK] Import Detection Bridge module")
        except ImportError as e:
            self.fail(f"Lỗi import detection_bridge: {e}")

    def test_07_import_web_routers_and_app(self):
        """Kiểm tra import ứng dụng web FastAPI (app.py) và các routers"""
        try:
            from app import app
            from backend.presentation.web.camera_views import camera_router
            from backend.presentation.web.weapon_views import weapon_router
            from backend.presentation.web.monitoring_views import monitoring_router
            self.assertIsNotNone(app)
            print("-> [OK] Import FastAPI Web application & Routers")
        except ImportError as e:
            self.fail(f"Lỗi import Web Routers & app: {e}")

if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    unittest.main()
