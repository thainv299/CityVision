import unittest
import numpy as np
import cv2
import json

from modules.parking.parking_logic import ViolationLogic, MOVING, WAITING, VIOLATION
from modules.parking.parking_manager import ParkingManager
from backend.infrastructure.ml.detection_bridge import _normalize_parking_zones, _to_polygon
from backend.application.use_cases.camera_use_cases import CameraUseCases


class TestMultiParkingROI(unittest.TestCase):
    """
    Bộ kiểm thử toàn diện cho tính năng Đa Vùng Cấm Đỗ Độc Lập (Multi-Zone ROI)
    và Cấu hình Thời gian Giới hạn Đỗ xe Riêng biệt theo từng Vùng.
    """

    def test_01_violation_logic_per_zone_timeout(self):
        """Kiểm tra ViolationLogic xử lý thời gian timeout riêng biệt cho từng xe/vùng"""
        fps = 10.0
        # Default stop_seconds = 10.0s (100 frames)
        logic = ViolationLogic(stop_seconds=10.0, move_thr_px=5.0, cooldown_seconds=2.0, fps=fps)

        # Xe 1 đỗ ở Vùng 1 (timeout = 2.0s = 20 frames)
        # Khởi tạo xe 1
        st, changed = logic.update(track_id=1, center=(50, 50), so_frame=0, stop_seconds=2.0)
        self.assertEqual(st, MOVING)

        # Frame 1: xe 1 đứng yên -> chuyển sang WAITING
        st, changed = logic.update(track_id=1, center=(50, 50), so_frame=1, stop_seconds=2.0)
        self.assertEqual(st, WAITING)
        self.assertTrue(changed)

        # Frame 15 (1.4s): xe 1 vẫn trong thời gian cho phép -> WAITING
        st, changed = logic.update(track_id=1, center=(50, 50), so_frame=15, stop_seconds=2.0)
        self.assertEqual(st, WAITING)
        self.assertFalse(changed)

        # Frame 22 (2.1s >= 2.0s): xe 1 vi phạm -> VIOLATION
        st, changed = logic.update(track_id=1, center=(50, 50), so_frame=22, stop_seconds=2.0)
        self.assertEqual(st, VIOLATION)
        self.assertTrue(changed)

        # Xe 2 đỗ ở Vùng 2 (timeout = 5.0s = 50 frames)
        logic.update(track_id=2, center=(300, 300), so_frame=0, stop_seconds=5.0)
        st, changed = logic.update(track_id=2, center=(300, 300), so_frame=1, stop_seconds=5.0)
        self.assertEqual(st, WAITING)

        # Ở frame 25 (2.4s): Xe 2 vẫn an toàn vì giới hạn là 5.0s
        st, changed = logic.update(track_id=2, center=(300, 300), so_frame=25, stop_seconds=5.0)
        self.assertEqual(st, WAITING)

        # Đến frame 55 (5.4s >= 5.0s): Xe 2 mới bị VIOLATION
        st, changed = logic.update(track_id=2, center=(300, 300), so_frame=55, stop_seconds=5.0)
        self.assertEqual(st, VIOLATION)
        print("-> [OK] Test 01: ViolationLogic per-zone timeout passed successfully!")

    def test_02_parking_manager_multi_zone_detection(self):
        """Kiểm tra ParkingManager phát hiện độc lập trên 2 vùng cấm khác nhau"""
        fps = 10.0
        pm = ParkingManager(None, None)
        pm.save_to_db = False
        pm.telegram_enabled = False
        pm.stop_seconds = 8.0  # Mặc định camera

        # Thiết lập 2 vùng cấm độc lập:
        # Vùng 1: Cổng trường (10, 10) -> (100, 100), timeout = 2s
        # Vùng 2: Vỉa hè đối diện (400, 400) -> (500, 500), timeout = 4s
        zone1_poly = np.array([[10, 10], [100, 10], [100, 100], [10, 100]], dtype=np.int32)
        zone2_poly = np.array([[400, 400], [500, 400], [500, 500], [400, 500]], dtype=np.int32)

        pm.set_parking_zones([
            {"id": 1, "name": "Cổng trường", "polygon": zone1_poly, "timeout": 2.0},
            {"id": 2, "name": "Vỉa hè đón khách", "polygon": zone2_poly, "timeout": 4.0}
        ])
        pm.setup_detection(fps)

        frame = np.zeros((600, 800, 3), dtype=np.uint8)
        clean_frame = frame.copy()

        # 1. Xe ID 10 nằm ở (50, 50) -> Bên trong Vùng 1
        # Frame 0: Khởi tạo track
        pm.process_vehicle(
            frame, clean_frame, track_id=10, label="car",
            cx=50, cy=50, frame_count=0,
            bbox=(40, 40, 60, 60), license_plate="29A-11111"
        )
        # Frame 1: Xe đứng yên -> chuyển sang WAITING
        label, color = pm.process_vehicle(
            frame, clean_frame, track_id=10, label="car",
            cx=50, cy=50, frame_count=1,
            bbox=(40, 40, 60, 60), license_plate="29A-11111"
        )
        self.assertIn("WAITING", str(label))
        self.assertEqual(pm.waiting_vehicles[10]["duration"], 2)
        self.assertEqual(pm.waiting_vehicles[10]["zone_name"], "Cổng trường")

        # Sau 25 frame (2.5s >= 2s): Xe 10 phải VI PHẠM
        for f in range(2, 26):
            label, color = pm.process_vehicle(
                frame, clean_frame, track_id=10, label="car",
                cx=50, cy=50, frame_count=f,
                bbox=(40, 40, 60, 60), license_plate="29A-11111"
            )
        self.assertIn("VIOLATION", str(label))

        # 2. Xe ID 20 nằm ở (450, 450) -> Bên trong Vùng 2 (timeout 4s)
        # Frame 0: Khởi tạo track
        pm.process_vehicle(
            frame, clean_frame, track_id=20, label="car",
            cx=450, cy=450, frame_count=0,
            bbox=(440, 440, 460, 460), license_plate="30B-22222"
        )
        # Frame 1: Chuyển sang WAITING
        label, color = pm.process_vehicle(
            frame, clean_frame, track_id=20, label="car",
            cx=450, cy=450, frame_count=1,
            bbox=(440, 440, 460, 460), license_plate="30B-22222"
        )
        self.assertIn("WAITING", str(label))
        self.assertEqual(pm.waiting_vehicles[20]["duration"], 4)
        self.assertEqual(pm.waiting_vehicles[20]["zone_name"], "Vỉa hè đón khách")

        # 3. Xe ID 30 nằm ở (250, 250) -> Nằm GIỮA 2 vùng (không thuộc vùng nào)
        label, color = pm.process_vehicle(
            frame, clean_frame, track_id=30, label="car",
            cx=250, cy=250, frame_count=1,
            bbox=(240, 240, 260, 260), license_plate="29C-33333"
        )
        # Nằm ngoài vùng cấm nên trả về None (không bị tính đỗ sai)
        self.assertIsNone(label)
        print("-> [OK] Test 02: ParkingManager multi-zone detection passed successfully!")

    def test_03_parking_manager_backward_compatibility(self):
        """Kiểm tra tương thích ngược khi code cũ gán trực tiếp pm.no_park_polygon"""
        pm = ParkingManager(None, None)
        poly = np.array([[0, 0], [100, 0], [100, 100], [0, 100]], dtype=np.int32)
        pm.no_park_polygon = poly

        # Kiểm tra getter
        self.assertIsNotNone(pm.no_park_polygon)
        self.assertEqual(len(pm.no_park_zones), 1)
        self.assertEqual(pm.no_park_zones[0]["name"], "Vùng cấm đỗ")
        np.testing.assert_array_equal(pm.no_park_polygon, poly)
        print("-> [OK] Test 03: ParkingManager backward compatibility passed!")

    def test_04_normalize_parking_zones_bridge(self):
        """Kiểm tra chuẩn hóa dữ liệu đa vùng từ settings và metadata sang pipe 640px"""
        # Metadata chứa 2 vùng tham chiếu từ canvas 800x450
        meta = {
            "units": "reference",
            "ref_width": 800,
            "ref_height": 450,
            "zones": [
                {
                    "id": 1,
                    "name": "Khu 1",
                    "points": [[100, 100], [200, 100], [200, 200], [100, 200]],
                    "timeout": 15
                },
                {
                    "id": 2,
                    "name": "Khu 2",
                    "points": [[400, 200], [500, 200], [500, 300], [400, 300]],
                    "timeout": 45
                }
            ]
        }

        # Scale lên frame kích thước 1600x900 (tỷ lệ 2.0x)
        zones = _normalize_parking_zones(raw_points=None, width=1600, height=900, metadata=meta)
        self.assertEqual(len(zones), 2)
        self.assertEqual(zones[0]["name"], "Khu 1")
        self.assertEqual(zones[0]["timeout"], 15.0)
        # Điểm [100, 100] scale x2 phải thành [200, 200]
        self.assertEqual(zones[0]["polygon"][0].tolist(), [200, 200])

        # Test fallback polygon đơn kiểu cũ
        legacy_pts = [[50, 50], [100, 50], [100, 100], [50, 100]]
        legacy_meta = {"units": "reference", "ref_width": 800, "ref_height": 450}
        fallback_zones = _normalize_parking_zones(raw_points=legacy_pts, width=800, height=450, metadata=legacy_meta)
        self.assertEqual(len(fallback_zones), 1)
        self.assertEqual(fallback_zones[0]["name"], "Vùng cấm")
        print("-> [OK] Test 04: Detection bridge _normalize_parking_zones passed!")

    def test_05_camera_use_cases_parsing(self):
        """Kiểm tra use case CameraUseCases parse và validate JSON đa vùng"""
        use_cases = CameraUseCases(camera_repo=None)
        
        multi_zone_json = json.dumps({
            "units": "reference",
            "ref_width": 800,
            "ref_height": 450,
            "zones": [
                {"id": 1, "name": "Vùng Cổng", "points": [[10, 10], [50, 10], [50, 50], [10, 50]], "timeout": 20},
                {"id": 2, "name": "Vùng Phụ", "points": [[100, 100], [150, 100], [150, 150], [100, 150]], "timeout": 40}
            ]
        })

        pts, meta = use_cases._parse_polygon(multi_zone_json)
        self.assertIsNotNone(pts)
        self.assertIn("zones", meta)
        self.assertEqual(len(meta["zones"]), 2)
        self.assertEqual(meta["zones"][0]["name"], "Vùng Cổng")
        self.assertEqual(meta["zones"][0]["timeout"], 20.0)
        self.assertEqual(meta["zones"][1]["timeout"], 40.0)
        print("-> [OK] Test 05: CameraUseCases polygon parsing passed!")

    def test_06_draw_polygon_overlay(self):
        """Kiểm tra hàm draw_polygon_overlay vẽ đầy đủ các vùng cấm, khử dấu tiếng Việt và vẽ nhãn"""
        from modules.parking.parking_manager import strip_vietnamese_accents
        self.assertEqual(strip_vietnamese_accents("Vùng cấm (30s)"), "Vung cam (30s)")
        self.assertEqual(strip_vietnamese_accents("Đỗ xe trái phép"), "Do xe trai phep")

        pm = ParkingManager(None, None)
        zone1_poly = np.array([[10, 10], [100, 10], [100, 100], [10, 100]], dtype=np.int32)
        zone2_poly = np.array([[200, 200], [300, 200], [300, 300], [200, 300]], dtype=np.int32)
        pm.set_parking_zones([
            {"id": 1, "name": "Vùng cấm 1", "polygon": zone1_poly, "timeout": 15},
            {"id": 2, "name": "Khu vực đỗ xe khẩn cấp", "polygon": zone2_poly, "timeout": 30}
        ])

        test_frame = np.zeros((400, 400, 3), dtype=np.uint8)
        # Không được ném Exception khi vẽ với tên tiếng Việt có dấu
        pm.draw_polygon_overlay(test_frame)
        self.assertGreater(test_frame.sum(), 0)
        print("-> [OK] Test 06: draw_polygon_overlay rendered without error and stripped accents cleanly!")


if __name__ == "__main__":
    unittest.main()
