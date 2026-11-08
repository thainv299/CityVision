import unittest
import time
import math
import numpy as np

from modules.parking.parking_manager import ParkingManager
from modules.parking.tracking_stabilizer import StationaryVehicleStabilizer, compute_box_iou


class TestParkingDedupAndGhost(unittest.TestCase):
    def setUp(self):
        self.pm = ParkingManager(None, None)
        poly = np.array([[100, 100], [500, 100], [500, 500], [100, 500]], dtype=np.int32)
        self.pm.set_parking_zones([{"id": 1, "name": "Vung Cam", "polygon": poly, "timeout": 30.0}])
        self.pm.setup_detection(20.0)
        self.pm.telegram_enabled = True
        self.pm.save_to_db = True

    def test_intra_frame_duplicate_canonical_id(self):
        """Kiểm tra: 2 box trên cùng 1 xe trong cùng 1 frame phải ánh xạ về cùng canonical ID"""
        stabilizer = StationaryVehicleStabilizer(iou_thresh=0.35, iom_thresh=0.50, max_ghost_frames=150)
        box1 = (150, 150, 350, 350)
        center1 = (250, 250)
        cid1 = stabilizer.get_canonical_id(14849, box1, center1, frame_index=10)
        self.assertEqual(cid1, 14849)

        # Box 2 cho cùng xe nhưng ByteTrack sinh ID 14880 (IoU > 0.8) trong cùng frame 10
        box2 = (152, 148, 352, 348)
        center2 = (252, 248)
        cid2 = stabilizer.get_canonical_id(14880, box2, center2, frame_index=10)
        self.assertEqual(cid2, 14849, "Box 2 trong cùng frame phải gộp về canonical ID của box 1!")

    def test_schedule_warning_spatial_dedup(self):
        """Kiểm tra: schedule_telegram_warning không được tạo 2 timer cho 2 xe đứng cùng vị trí"""
        fake_frame = np.zeros((600, 800, 3), dtype=np.uint8)
        # Lên lịch cảnh báo cho xe 1 (ID 14849) tại (250, 250)
        self.pm.schedule_telegram_warning(14849, fake_frame, "Warning {PLATE}", center=(250, 250), delay=15.0)
        self.assertIn(14849, self.pm.pending_telegram_warnings)

        # Lên lịch cảnh báo cho xe 2 (ID 14880) tại (255, 252) (cách 5px)
        self.pm.schedule_telegram_warning(14880, fake_frame, "Warning {PLATE}", center=(255, 252), delay=15.0)
        # ID 14880 không được tạo timer trùng lặp!
        self.assertNotIn(14880, self.pm.pending_telegram_warnings)
        self.assertEqual(len(self.pm.pending_telegram_warnings), 1)

    def test_waiting_vehicles_spatial_merge(self):
        """Kiểm tra: Xe thứ 2 vào WAITING tại cùng vị trí phải gộp vào xe thứ 1 (không tạo entry trùng)
        
        Lưu ý: Tầng 1 (cảnh báo 15s sớm) đã bị loại bỏ theo yêu cầu.
        Test này chỉ kiểm tra rằng waiting_vehicles không bị nhân đôi.
        """
        fake_frame = np.zeros((600, 800, 3), dtype=np.uint8)
        bbox1 = (150, 150, 350, 350)
        cx1, cy1 = 250, 250

        # Cho ID 1000 dừng lại -> vào WAITING
        self.pm.process_vehicle(fake_frame, fake_frame, 1000, "car", cx1, cy1, 1, bbox=bbox1)
        self.pm.process_vehicle(fake_frame, fake_frame, 1000, "car", cx1, cy1, 2, bbox=bbox1)
        self.assertIn(1000, self.pm.waiting_vehicles)
        # Tầng 1 đã bị loại bỏ: không còn pending_telegram_warnings khi vào WAITING
        self.assertNotIn(1000, self.pm.pending_telegram_warnings,
                         "Tầng 1 đã bị loại bỏ: không được lên lịch cảnh báo 15s sớm khi vào WAITING")

        # Xe ID 2000 xuất hiện tại cùng vị trí (cách 10px) và dừng lại
        bbox2 = (155, 155, 355, 355)
        cx2, cy2 = 255, 255
        self.pm.process_vehicle(fake_frame, fake_frame, 2000, "car", cx2, cy2, 1, bbox=bbox2)
        self.pm.process_vehicle(fake_frame, fake_frame, 2000, "car", cx2, cy2, 2, bbox=bbox2)

        # ID 2000 phải được ánh xạ vào ID 1000
        self.assertEqual(self.pm.track_id_map.get(2000), 1000)
        # Chỉ có đúng 1 xe trong waiting_vehicles (không bị nhân đôi)
        self.assertEqual(len(self.pm.waiting_vehicles), 1)
        # Không có cảnh báo sớm nào được lên lịch (Tầng 1 đã bị loại)
        self.assertEqual(len(self.pm.pending_telegram_warnings), 0)

    def test_ghost_track_purge_after_7_minutes(self):
        """Kiểm tra: Xe rời đi sau 7 phút (420s) phải bị xóa sạch, xe mới không bị gán lại ID cũ!"""
        fake_frame = np.zeros((600, 800, 3), dtype=np.uint8)
        t0 = 1000.0
        # Giả lập thời gian
        import modules.parking.parking_manager as pm_mod
        orig_time = pm_mod.time.time

        try:
            # Xe 1 (xe trắng, ID 14880) đỗ ở t0
            pm_mod.time.time = lambda: t0
            bbox_white = (150, 150, 350, 350)
            self.pm.process_vehicle(fake_frame, fake_frame, 14880, "car", 250, 250, 1, bbox=bbox_white)
            self.pm.process_vehicle(fake_frame, fake_frame, 14880, "car", 250, 250, 2, bbox=bbox_white)
            self.assertIn(14880, self.pm.last_seen)

            # 7 phút sau (t0 + 420s), xe trắng đã đi mất từ lâu
            t_7min_later = t0 + 420.0
            pm_mod.time.time = lambda: t_7min_later

            # Dọn dẹp stale tracks
            self.pm.cleanup_stale_tracks(t_7min_later)
            self.assertNotIn(14880, self.pm.last_seen)
            self.assertNotIn(14880, self.pm.ghost_tracks)
            self.assertNotIn(14880, self.pm.waiting_vehicles)

            # Xe 2 (xe hồng, ID 19000) đỗ vào đúng vị trí đó
            bbox_pink = (155, 152, 352, 348)
            self.pm.process_vehicle(fake_frame, fake_frame, 19000, "car", 252, 250, 100, bbox=bbox_pink)
            self.pm.process_vehicle(fake_frame, fake_frame, 19000, "car", 252, 250, 101, bbox=bbox_pink)

            # ID xe hồng KHÔNG ĐƯỢC PHÉP bị Re-ID thành 14880!
            self.assertEqual(self.pm.track_id_map.get(19000, 19000), 19000, "Xe hồng sau 7 phút không được bị nhận nhầm thành xe trắng cũ!")
            self.assertIn(19000, self.pm.waiting_vehicles)
            self.assertNotIn(14880, self.pm.waiting_vehicles)
        finally:
            pm_mod.time.time = orig_time


if __name__ == '__main__':
    unittest.main()
