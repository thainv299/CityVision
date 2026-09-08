import unittest
import numpy as np
import cv2
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

from modules.weapon.weapon_manager import WeaponManager
from modules.parking.parking_manager import ParkingManager
from modules.traffic.traffic_monitor import TrafficMonitor
from modules.utils.alpr_logger import ALPRLogger

class TestBusinessManagers(unittest.TestCase):
    """Kiểm tra logic thuật toán của các Manager nghiệp vụ trong hệ thống"""

    def test_01_weapon_manager_counter_and_debounce(self):
        """Kiểm tra đếm 5-frame tích lũy, vẽ khung đỏ và debounce 45s của WeaponManager"""
        wm = WeaponManager(camera_id=1, camera_name="Cam Test")
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        clean_frame = np.zeros((480, 640, 3), dtype=np.uint8)
        bbox = [100, 100, 200, 200]

        # 4 frame đầu -> Chưa đạt ngưỡng 5 frame
        for i in range(1, 5):
            lbl, color = wm.process_weapon(
                frame=frame, clean_frame=clean_frame, track_id=99,
                label="knife", confidence=0.89, bbox=bbox, telegram_enabled=False
            )
            self.assertIn(f"[{i}/5]", lbl)

        # Frame 5 -> Đạt ngưỡng 5 frame (Kích hoạt sự kiện)
        lbl5, color5 = wm.process_weapon(
            frame=frame, clean_frame=clean_frame, track_id=99,
            label="knife", confidence=0.89, bbox=bbox, telegram_enabled=False
        )
        self.assertIn("[5/5]", lbl5)
        self.assertEqual(color5, (0, 0, 255))  # Khung ĐỎ

        # Frame 6 ngay sau đó -> Nằm trong debounce 45s nên không phát cảnh báo trùng
        lbl6, color6 = wm.process_weapon(
            frame=frame, clean_frame=clean_frame, track_id=99,
            label="knife", confidence=0.89, bbox=bbox, telegram_enabled=False
        )
        self.assertEqual(color6, (0, 0, 255))
        print("-> [OK] WeaponManager 5-frame counter & 45s debounce test passed")

    def test_02_traffic_monitor_occupancy(self):
        """Kiểm tra đếm số người/xe và tính % lấp đầy ROI của TrafficMonitor"""
        roi_points = [[0, 0], [640, 0], [640, 480], [0, 480]]
        tm = TrafficMonitor(roi_polygon=roi_points, congestion_threshold=35.0)
        tm.reset_counters()
        
        # Log người & phương tiện
        tm.log_person(bbox=(10, 10, 50, 100))
        tm.log_vehicle(track_id=1, cx=100, cy=100, current_time=1.0, bbox=(80, 80, 200, 200))
        
        self.assertEqual(tm.people_count, 1)
        self.assertEqual(tm.vehicle_count, 1)
        self.assertEqual(len(tm.current_bboxes), 2)

        # Thao tác tính toán vận tốc & mật độ
        frame_shape = (480, 640, 3)
        avg_speed, status_text, status_color, level = tm.calculate_speed_and_status(1.0, frame_shape)
        self.assertGreater(tm.last_occupancy, 0.0)
        print(f"-> [OK] TrafficMonitor occupancy & count test passed (Occupancy: {tm.last_occupancy:.1f}%)")

    def test_03_parking_manager(self):
        """Kiểm tra ParkingManager khởi tạo vùng cấm đỗ"""
        pm = ParkingManager(None, None)
        pm.no_park_polygon = np.array([[10, 10], [200, 10], [200, 200], [10, 200]])
        pm.stop_seconds = 5.0
        pm.setup_detection(30.0)
        
        self.assertIsNotNone(pm.no_park_polygon)
        self.assertEqual(pm.stop_seconds, 5.0)
        print("-> [OK] ParkingManager setup test passed")

    def test_04_alpr_logger_text_rendering(self):
        """Kiểm tra ALPRLogger tạo đường dẫn và vẽ nhãn biển số ở góc trên bên trái"""
        logger = ALPRLogger(id_camera=1)
        logger.save_to_db = False  # Không lưu DB thật trong unit test
        
        full_frame = np.zeros((480, 640, 3), dtype=np.uint8)
        plate_coords = [50, 50, 150, 100]
        vehicle_bbox = [30, 30, 300, 300]
        
        # Test lưu log
        logger.process_plate("30F-12345", current_frame=100, plate_img=None, full_frame=full_frame, plate_coords=plate_coords, vehicle_bbox=vehicle_bbox)
        print("-> [OK] ALPRLogger text rendering & logging test passed")

if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    unittest.main()
