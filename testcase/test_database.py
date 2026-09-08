import unittest
import json
import os
import sys
from pathlib import Path

# Setup paths
PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from backend.database.sqlite_db import (
    connect, init_db, log_weapon_detection, get_weapon_detections,
    resolve_weapon_detection, delete_weapon_detection,
    log_parking_violation, get_illegal_parking_violations, resolve_parking_violation,
    log_congestion, update_congestion_end_time,
    log_detected_license_plate, get_detected_license_plates,
    get_system_setting, update_system_setting
)
from backend.domain.entities.camera import Camera
from backend.database.sqlite_camera_repo import SqliteCameraRepository
from backend.domain.entities.user import User
from backend.database.sqlite_user_repo import SqliteUserRepository

class TestDatabaseAndRepositories(unittest.TestCase):
    """Kiểm tra chức năng CSDL SQLite, Bảng dữ liệu & Repositories"""

    @classmethod
    def setUpClass(cls):
        """Khởi tạo CSDL trước khi chạy các test case"""
        init_db()

    def test_01_db_connection(self):
        """Kiểm tra kết nối tới CSDL SQLite"""
        with connect() as conn:
            row = conn.execute("SELECT 1 as test").fetchone()
            self.assertEqual(row["test"], 1)
        print("-> [OK] SQLite connection test passed")

    def test_02_system_settings_kv(self):
        """Kiểm tra đọc/ghi cài đặt hệ thống key-value (cai_dat_he_thong)"""
        labels = ["person", "car", "knife", "pistol", "sword"]
        update_system_setting("test_enabled_labels", json.dumps(labels))
        val = get_system_setting("test_enabled_labels")
        self.assertIsNotNone(val)
        self.assertEqual(json.loads(val), labels)
        print("-> [OK] System settings KV read/write test passed")

    def test_03_camera_repository(self):
        """Kiểm tra Camera Repository (thêm, sửa, toggle weapon detection, lấy danh sách)"""
        repo = SqliteCameraRepository()
        cameras = repo.list_all()
        self.assertTrue(isinstance(cameras, list))
        
        if cameras:
            cam = cameras[0]
            orig_weapon_setting = cam.enable_weapon_detection
            
            # Toggle setting
            cam.enable_weapon_detection = not orig_weapon_setting
            updated = repo.update(cam)
            self.assertEqual(updated.enable_weapon_detection, not orig_weapon_setting)
            
            # Revert back
            cam.enable_weapon_detection = orig_weapon_setting
            repo.update(cam)
            print(f"-> [OK] Camera Repository test passed (Camera ID {cam.id})")

    def test_04_user_repository(self):
        """Kiểm tra User Repository (đếm admin, danh sách người dùng)"""
        repo = SqliteUserRepository()
        users = repo.list_all()
        self.assertTrue(isinstance(users, list))
        admin_count = repo.count_admin()
        self.assertGreaterEqual(admin_count, 1)
        print(f"-> [OK] User Repository test passed ({len(users)} users, {admin_count} admins)")

    def test_05_weapon_detection_crud(self):
        """Kiểm tra CRUD Phát hiện Vũ khí (phat_hien_vu_khi)"""
        # Create
        rec_id = log_weapon_detection(
            camera_id=1,
            loai_vu_khi="pistol",
            thoi_gian_phat_hien="2026-09-08 21:00:00",
            do_chinh_xac=0.95,
            frame_path="logs/weapons/test_unit.jpg"
        )
        self.assertIsNotNone(rec_id)
        
        # Read
        records = get_weapon_detections(limit=10)
        found = any(r['id'] == rec_id and r['weapon_type'] == "pistol" for r in records)
        self.assertTrue(found)

        # Resolve
        resolved = resolve_weapon_detection(rec_id)
        self.assertTrue(resolved)

        # Delete
        deleted = delete_weapon_detection(rec_id)
        self.assertTrue(deleted)
        print("-> [OK] Weapon detection CRUD test passed")

    def test_06_parking_violation_crud(self):
        """Kiểm tra CRUD Vi phạm Đỗ xe (vi_pham_do_xe)"""
        rec_id = log_parking_violation(
            camera_id=1,
            license_plate="29A-12345",
            frame_path="logs/violations/test_park.jpg"
        )
        self.assertIsNotNone(rec_id)

        resolved = resolve_parking_violation(rec_id)
        self.assertTrue(resolved)
        print("-> [OK] Parking violation CRUD test passed")

    def test_07_congestion_log(self):
        """Kiểm tra Nhật ký Ùn tắc (nhat_ky_un_tac)"""
        rec_id = log_congestion(camera_id=1, level=2, duong_dan_anh="logs/traffic/test_cong.jpg")
        self.assertIsNotNone(rec_id)
        update_congestion_end_time(rec_id)
        print("-> [OK] Traffic congestion log test passed")

    def test_08_alpr_license_plate_log(self):
        """Kiểm tra Nhật ký Biển số xe (bien_so_phat_hien)"""
        log_detected_license_plate(
            license_plate="30F-99999",
            detection_count=1,
            avg_confidence=0.92,
            image_paths="logs/plates/test_plate.jpg",
            camera_id=1
        )
        plates = get_detected_license_plates(search_query="30F-99999")
        self.assertTrue(len(plates) > 0)
        print("-> [OK] ALPR license plate log test passed")

if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    unittest.main()
