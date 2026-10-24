import unittest
import numpy as np
import cv2
import json

from modules.parking.parking_logic import ViolationLogic, MOVING, WAITING, VIOLATION
from modules.parking.parking_manager import ParkingManager
from modules.parking.tracking_stabilizer import StationaryVehicleStabilizer, compute_box_iou
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
        pm.MIN_NOTIFY_SECONDS = 0.0  # Tắt guard tối thiểu trong unit test per-zone logic

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

    def test_07_compute_box_iou_and_iom(self):
        """Kiểm tra tính toán IoU và IoM giữa hai bounding box"""
        # Trùng khớp hoàn toàn
        b1 = (100, 100, 200, 200)
        iou, iom = compute_box_iou(b1, b1)
        self.assertAlmostEqual(iou, 1.0)
        self.assertAlmostEqual(iom, 1.0)

        # Rời nhau hoàn toàn
        b2 = (300, 300, 400, 400)
        iou_disjoint, iom_disjoint = compute_box_iou(b1, b2)
        self.assertEqual(iou_disjoint, 0.0)
        self.assertEqual(iom_disjoint, 0.0)

        # Đè một nửa
        b3 = (150, 100, 200, 200) # w=50, h=100 -> area=5000, nằm trọn trong b1 (area=10000)
        iou_half, iom_half = compute_box_iou(b1, b3)
        self.assertAlmostEqual(iom_half, 1.0) # IoM = 5000 / min(10000, 5000) = 1.0
        self.assertAlmostEqual(iou_half, 0.5) # IoU = 5000 / 10000 = 0.5
        print("-> [OK] Test 07: compute_box_iou and compute_box_iom passed!")

    def test_08_stationary_vehicle_stabilizer_id_switch(self):
        """Kiểm tra StationaryVehicleStabilizer giữ nguyên canonical ID khi ByteTrack nhảy ID (17 -> 23)"""
        stabilizer = StationaryVehicleStabilizer(iou_thresh=0.35, iom_thresh=0.50, max_ghost_frames=150)
        
        # Frame 1: Xe xuất hiện với raw_id = 17
        bbox_f1 = (100, 100, 250, 300)
        center_f1 = (175, 200)
        c1 = stabilizer.get_canonical_id(17, bbox_f1, center_f1, frame_index=1)
        self.assertEqual(c1, 17)

        # Gọi nhiều lần trong cùng frame 1 (Pass 1, Main loop, OCR): ID không được nhảy!
        c1_repeat1 = stabilizer.get_canonical_id(17, bbox_f1, center_f1, frame_index=1)
        c1_repeat2 = stabilizer.get_canonical_id(17, bbox_f1, center_f1, frame_index=1)
        self.assertEqual(c1_repeat1, 17)
        self.assertEqual(c1_repeat2, 17)

        # Frame 30: Xe đỗ tại chỗ, raw_id vẫn là 17
        c2 = stabilizer.get_canonical_id(17, bbox_f1, center_f1, frame_index=30)
        self.assertEqual(c2, 17)

        # Frame 80: ByteTrack tạm mất dấu và gán raw_id mới = 23 cho cùng vị trí xe đỗ
        bbox_f80 = (102, 98, 252, 298)
        center_f80 = (177, 198)
        c3 = stabilizer.get_canonical_id(23, bbox_f80, center_f80, frame_index=80)
        # Canonical ID phải được giữ nguyên là 17 thay vì nhảy sang 23
        self.assertEqual(c3, 17)
        print("-> [OK] Test 08: StationaryVehicleStabilizer ID retention (ID switch) passed!")

    def test_09_stationary_vehicle_stabilizer_plate_fusion(self):
        """Kiểm tra StationaryVehicleStabilizer tự động sáp nhập track khi đọc cùng 1 biển số xe"""
        stabilizer = StationaryVehicleStabilizer()

        # Xe 1 đọc được biển số 15B-33178
        c1 = stabilizer.get_canonical_id(10, (50, 50, 150, 150), (100, 100), frame_index=1, plate="15B-33178")
        self.assertEqual(c1, 10)

        # Xe 2 (raw_id = 20) ở frame sau cũng đọc ra biển số 15B-33178
        c2 = stabilizer.get_canonical_id(20, (55, 52, 155, 152), (105, 102), frame_index=20, plate="15B-33178")
        # Phải sáp nhập về canonical ID 10
        self.assertEqual(c2, 10)
        print("-> [OK] Test 09: StationaryVehicleStabilizer plate-based track fusion passed!")

    def test_10_intra_frame_duplicate_box_suppression(self):
        """Kiểm tra lọc bỏ box trùng lặp trên cùng 1 xe trong cùng frame (Intra-frame NMS)"""
        class MockBox:
            def __init__(self, xyxy, conf):
                self.xyxy = [np.array(xyxy)]
                self.conf = [np.array(conf)]

        stabilizer = StationaryVehicleStabilizer()
        box_main = MockBox((100, 100, 300, 300), 0.90)
        box_dup = MockBox((104, 102, 298, 302), 0.65)  # Trùng lặp với box_main, conf thấp hơn
        box_other = MockBox((500, 500, 650, 650), 0.88) # Xe khác hoàn toàn

        boxes = [box_main, box_dup, box_other]
        suppressed = stabilizer.filter_duplicate_indices(boxes, [0, 1, 2])
        # Box dup (index 1) phải bị loại bỏ
        self.assertEqual(suppressed, {1})

        filtered_boxes = stabilizer.filter_duplicate_boxes(boxes)
        self.assertEqual(len(filtered_boxes), 2)
        self.assertIn(box_main, filtered_boxes)
        self.assertIn(box_other, filtered_boxes)
        self.assertNotIn(box_dup, filtered_boxes)
        print("-> [OK] Test 10: Intra-frame duplicate box suppression passed!")

    def test_11_parking_manager_plate_track_fusion_and_debounce(self):
        """Kiểm tra ParkingManager sáp nhập track khi update_plate trùng xe và debounce vi phạm 15 phút"""
        pm = ParkingManager(None, None)
        pm.stop_seconds = 5.0

        zone = np.array([[0, 0], [500, 0], [500, 500], [0, 500]], dtype=np.int32)
        pm.set_parking_zones([{"id": 1, "name": "Vung Cam", "polygon": zone, "timeout": 5.0}])

        # Giả lập xe 10 đang đếm thời gian đỗ, có biển số 15B33178
        pm.waiting_vehicles[10] = {'first_seen': 1.0, 'plate': "15B33178"}

        # Giả lập xe 20 vô tình được tạo với biển số chưa rõ
        pm.waiting_vehicles[20] = {'first_seen': 2.0, 'plate': None}

        # OCR đọc ra biển số cho xe 20 là 15B33178
        pm.update_plate(20, "15B33178")

        # Track 20 phải được map vào Track 10
        self.assertEqual(pm.track_id_map.get(20), 10)
        # Bộ đếm trùng lặp cho xe 20 phải bị hủy
        self.assertNotIn(20, pm.waiting_vehicles)

        print("-> [OK] Test 11: ParkingManager plate track fusion passed!")

    def test_12_two_stage_notification_logic(self):
        """
        Kiểm tra logic thông báo 2 tầng:
        - Tầng 1: Xe đỗ 15s -> cảnh báo sớm "xe đang đỗ" (schedule_telegram_warning, delay=15s)
        - Tầng 2: Xe đỗ đủ zone_timeout -> thông báo vi phạm (_schedule_violation_alert, dedup riêng)
        - _check_notify_dedup và _check_violation_dedup là 2 registry riêng biệt
        """
        import time as _time

        fps = 10.0
        pm = ParkingManager(None, None)
        pm.save_to_db = True
        pm.telegram_enabled = True
        pm.tts_enabled = False
        pm.MIN_NOTIFY_SECONDS = 0.0  # Tắt guard tối thiểu để test với zone_timeout nhỏ
        pm.stop_seconds = 2.0

        zone_poly = np.array([[0, 0], [200, 0], [200, 200], [0, 200]], dtype=np.int32)
        pm.set_parking_zones([{"id": 1, "name": "Zone A", "polygon": zone_poly, "timeout": 2.0}])
        pm.setup_detection(fps)

        frame = np.zeros((400, 400, 3), dtype=np.uint8)
        clean_frame = frame.copy()

        # --- Bước 1: Frame 0 (MOVING)
        pm.process_vehicle(frame, clean_frame, track_id=1, label="car",
                           cx=100, cy=100, frame_count=0, bbox=(90, 90, 110, 110),
                           license_plate="51A-00001")

        # --- Bước 2: Frame 1 (chuyển sang WAITING) -> Tầng 1: phải lên lịch cảnh báo sớm 15s
        pm.process_vehicle(frame, clean_frame, track_id=1, label="car",
                           cx=100, cy=100, frame_count=1, bbox=(90, 90, 110, 110),
                           license_plate="51A-00001")

        self.assertIn(1, pm.waiting_vehicles, "Xe phải vào waiting_vehicles")
        # Tầng 1: phải có pending_telegram_warnings (timer 15s đã được đặt)
        self.assertIn(1, pm.pending_telegram_warnings,
                      "Phải lên lịch cảnh báo sớm (15s) khi xe chuyển sang WAITING")
        # Delay của timer phải là 15s
        warning_timer = pm.pending_telegram_warnings[1]['timer']
        self.assertAlmostEqual(warning_timer.interval, 15.0, places=1,
                               msg="Timer cảnh báo sớm phải là 15s")

        # --- Bước 3: Xe đỗ đủ zone_timeout -> VIOLATION -> Tầng 2
        for f in range(2, 25):
            label, color = pm.process_vehicle(frame, clean_frame, track_id=1, label="car",
                                              cx=100, cy=100, frame_count=f,
                                              bbox=(90, 90, 110, 110), license_plate="51A-00001")

        self.assertIn("VIOLATION", str(label), "Xe phải chuyển sang VIOLATION sau 2s")
        # pending_telegram_warnings có thể vẫn còn (timer 15s chưa fire) hoặc đã cancel khi VIOLATION
        # Quan trọng hơn: active_recordings phải có data
        self.assertIn(1, pm.active_recordings, "Xe vi phạm phải vào active_recordings")

        # Hủy timer cảnh báo sớm (nếu còn) để tránh thread leak trong test
        pm.cancel_pending_warning(1)


        # --- Bước 4: Kiểm tra 2 registry dedup RIÊNG BIỆT ---
        pm2 = ParkingManager(None, None)
        pm2.MIN_NOTIFY_SECONDS = 0.0

        # 4a. Gửi cảnh báo sớm (warning) cho biển "51A-00001" -> OK, ghi vào _last_notified_plates
        dup_w1 = pm2._check_notify_dedup(1, cx=100.0, cy=100.0, plate="51A-00001")
        self.assertFalse(dup_w1, "Lần đầu warning không được bị block")

        # 4b. Warning lần 2 cùng biển số -> bị block bởi warning registry
        dup_w2 = pm2._check_notify_dedup(1, cx=100.0, cy=100.0, plate="51A-00001")
        self.assertTrue(dup_w2, "Warning lần 2 cùng biển số phải bị block")

        # 4c. VI PHẠM cùng biển số -> KHÔNG bị block vì dùng registry riêng (violation registry)
        dup_v1 = pm2._check_violation_dedup(1, cx=100.0, cy=100.0, plate="51A-00001")
        self.assertFalse(dup_v1,
                         "Violation dedup KHÔNG bị block bởi warning dedup (registry riêng biệt)")

        # 4d. Vi phạm lần 2 cùng biển số -> bị block bởi violation registry
        dup_v2 = pm2._check_violation_dedup(1, cx=100.0, cy=100.0, plate="51A-00001")
        self.assertTrue(dup_v2, "Violation lần 2 cùng biển số phải bị block")

        # 4e. Vị trí khác hoàn toàn, biển số mới -> cả 2 registry đều OK
        dup_new_w = pm2._check_notify_dedup(2, cx=350.0, cy=350.0, plate="33B-99999")
        dup_new_v = pm2._check_violation_dedup(2, cx=350.0, cy=350.0, plate="33B-99999")
        self.assertFalse(dup_new_w, "Vị trí/biển số mới: warning không bị block")
        self.assertFalse(dup_new_v, "Vị trí/biển số mới: violation không bị block")

        # --- Bước 5: MIN_NOTIFY_SECONDS là ngưỡng tối thiểu của zone_timeout
        pm3 = ParkingManager(None, None)
        pm3.MIN_NOTIFY_SECONDS = 15.0

        short_zone_poly = np.array([[0, 0], [100, 0], [100, 100], [0, 100]], dtype=np.int32)
        pm3.set_parking_zones([
            {"id": 1, "name": "Zone B", "polygon": short_zone_poly, "timeout": 5.0},   # < 15s -> clamp
            {"id": 2, "name": "Zone C", "polygon": short_zone_poly, "timeout": 30.0},  # >= 15s -> giữ nguyên
        ])
        self.assertAlmostEqual(pm3.no_park_zones[0]["timeout"], 15.0, places=1,
                               msg="zone_timeout < MIN_NOTIFY_SECONDS phải được clamp lên 15s")
        self.assertAlmostEqual(pm3.no_park_zones[1]["timeout"], 30.0, places=1,
                               msg="zone_timeout >= MIN_NOTIFY_SECONDS giữ nguyên")

        print("-> [OK] Test 12: Two-stage notification (Warning@15s + Violation@zone_timeout); separate dedup registries; MIN_NOTIFY_SECONDS zone floor!")


if __name__ == "__main__":
    unittest.main()

