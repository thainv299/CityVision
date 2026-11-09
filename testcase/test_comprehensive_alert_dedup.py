import unittest
import time
import os
import cv2
import numpy as np
from unittest.mock import patch, MagicMock

from backend.database.sqlite_db import check_and_record_telegram_dedup
from modules.utils.interactive_telegram_bot import send_alert_with_button, send_clear_alert
from modules.utils.telegram_bot import send_telegram_image, send_telegram_video
from modules.utils.traffic_alert_manager import TrafficAlertManager
from modules.parking.parking_manager import ParkingManager
from modules.parking.tracking_stabilizer import StationaryVehicleStabilizer


class TestComprehensiveAlertDedup(unittest.TestCase):

    def test_01_atomic_sqlite_dedup_guard(self):
        """Kiểm tra hàm check_and_record_telegram_dedup chống gửi trùng xuyên tiến trình/luồng."""
        key = f"unit_test_key_{int(time.time()*1000)}"
        
        # Lần 1: Chưa gửi -> Cho phép gửi (return False)
        res1 = check_and_record_telegram_dedup(key, cooldown_seconds=10.0)
        self.assertFalse(res1, "Lần 1 phải cho phép gửi (không trùng)")
        
        # Lần 2 (ngay sau đó): Phải bị chặn (return True)
        res2 = check_and_record_telegram_dedup(key, cooldown_seconds=10.0)
        self.assertTrue(res2, "Lần 2 phải bị chặn do đang trong cooldown")
        
        # Key khác -> Phải cho phép gửi
        other_key = f"{key}_diff"
        res_other = check_and_record_telegram_dedup(other_key, cooldown_seconds=10.0)
        self.assertFalse(res_other, "Key khác phải cho phép gửi")

    @patch("modules.utils.interactive_telegram_bot.bot")
    @patch("modules.utils.interactive_telegram_bot.TELEGRAM_CHAT_ID", "123456789")
    def test_02_interactive_bot_dedup(self, mock_bot):
        """Kiểm tra send_alert_with_button và send_clear_alert tự động chặn thông báo trùng lặp."""
        dummy_img = "test_alert.jpg"
        cv2.imwrite(dummy_img, np.zeros((100, 100, 3), dtype=np.uint8))
        try:
            uniq_id = int(time.time() * 1000)
            caption = f"CẢNH BÁO MỨC ĐỘ 2 TEST {uniq_id}"
            clear_caption = f"THÔNG BÁO THÔNG THOÁNG TEST {uniq_id}"

            # 1. Test alert with button
            send_alert_with_button(dummy_img, caption, level=2)
            self.assertEqual(mock_bot.send_photo.call_count, 1)

            # Gọi lại lần 2 ngay lập tức -> Bị chặn
            send_alert_with_button(dummy_img, caption, level=2)
            self.assertEqual(mock_bot.send_photo.call_count, 1, "send_alert_with_button lần 2 phải bị chặn")

            # 2. Test clear alert
            send_clear_alert(clear_caption)
            self.assertEqual(mock_bot.send_message.call_count, 1)

            # Gọi lại lần 2 ngay lập tức -> Bị chặn
            send_clear_alert(clear_caption)
            self.assertEqual(mock_bot.send_message.call_count, 1, "send_clear_alert lần 2 phải bị chặn")
        finally:
            if os.path.exists(dummy_img):
                os.remove(dummy_img)

    @patch("requests.post")
    def test_03_telegram_bot_image_and_video_dedup(self, mock_post):
        """Kiểm tra send_telegram_image và send_telegram_video chặn gửi trùng."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_post.return_value = mock_resp

        dummy_img = "test_img.jpg"
        cv2.imwrite(dummy_img, np.zeros((50, 50, 3), dtype=np.uint8))
        try:
            uniq_id = int(time.time() * 1000)
            img_caption = f"Test warning photo {uniq_id}"
            
            # Gửi lần 1 -> Thành công
            res1 = send_telegram_image(dummy_img, img_caption, bot_token="fake", chat_id="123")
            self.assertTrue(res1)
            self.assertEqual(mock_post.call_count, 1)

            # Gửi lần 2 -> Bị dedup chặn
            res2 = send_telegram_image(dummy_img, img_caption, bot_token="fake", chat_id="123")
            self.assertFalse(res2)
            self.assertEqual(mock_post.call_count, 1, "send_telegram_image lần 2 phải bị chặn")
        finally:
            if os.path.exists(dummy_img):
                os.remove(dummy_img)

    @patch("modules.utils.traffic_alert_manager.send_alert_with_button")
    @patch("modules.utils.traffic_alert_manager.send_clear_alert")
    def test_04_traffic_alert_manager_preview_mode(self, mock_clear, mock_alert):
        """Kiểm tra TrafficAlertManager không bao giờ gửi Telegram khi preview/test (telegram_enabled=False)."""
        tam = TrafficAlertManager()
        tam.camera_id = 99
        tam.telegram_enabled = False  # Chế độ test/preview

        clean_frame = np.zeros((480, 640, 3), dtype=np.uint8)
        tam._trigger_alert(level=2, frame=clean_frame)
        self.assertEqual(mock_alert.call_count, 0, "Không được gửi alert khi telegram_enabled=False")

        tam._trigger_clear_notification()
        self.assertEqual(mock_clear.call_count, 0, "Không được gửi clear alert khi telegram_enabled=False")

    def test_05_parking_manager_ghost_recovery_and_new_car_distinction(self):
        """
        1. Xe đỗ bị che khuất ngắn (< 5s) -> Khôi phục về ID cũ.
        2. Xe đến sau một lúc (> 5s) -> Coi là xe mới hoàn toàn, KHÔNG bị gán ID cũ!
        """
        pm = ParkingManager(None, None)
        pm.setup_detection(fps=30.0)
        pm.camera_id = 1
        pm.telegram_enabled = True
        pm.save_to_db = True

        # Vùng cấm: [100, 100] đến [500, 500]
        poly = np.array([[100, 100], [500, 100], [500, 500], [100, 500]], dtype=np.int32)
        pm.set_parking_zones([{"polygon": poly, "duration": 30, "name": "Vùng Cấm"}])

        frame = np.zeros((600, 800, 3), dtype=np.uint8)
        clean_frame = frame.copy()

        # Xe 1 (ID 85460) đỗ tại (250, 250)
        bbox1 = (200, 200, 300, 300)
        cx1, cy1 = 250, 250
        pm.process_vehicle(frame, clean_frame, 85460, "car", cx1, cy1, frame_count=1, bbox=bbox1)
        pm.process_vehicle(frame, clean_frame, 85460, "car", cx1, cy1, frame_count=2, bbox=bbox1)
        self.assertIn(85460, pm.waiting_vehicles)

        # Che khuất ngắn 2 giây -> ghost
        t_ghost = time.time() + 2.0
        pm.cleanup_stale_tracks(t_ghost)
        self.assertIn(85460, pm.ghost_tracks)

        # Xe xuất hiện lại ngay sau 2s tại cùng vị trí (252, 251) với ID mới 174528 -> Khôi phục về 85460
        bbox2 = (202, 201, 302, 301)
        cx2, cy2 = 252, 251
        pm.process_vehicle(frame, clean_frame, 174528, "car", cx2, cy2, frame_count=50, bbox=bbox2)
        self.assertEqual(pm.track_id_map.get(174528), 85460, "Che khuất ngắn phải khôi phục về ID cũ")

        # Bây giờ xe 85460 rời đi: qua 6 giây không thấy
        t_left = t_ghost + 7.0
        pm.cleanup_stale_tracks(t_left)
        self.assertNotIn(85460, pm.ghost_tracks, "Quá 5s xe phải bị purge hoàn toàn")
        self.assertNotIn(85460, pm.waiting_vehicles)

        # Xe 3 (ID 99999) đến sau đó tại cùng vị trí đỗ (250, 250)
        bbox3 = (200, 200, 300, 300)
        pm.process_vehicle(frame, clean_frame, 99999, "car", 250, 250, frame_count=200, bbox=bbox3)
        pm.process_vehicle(frame, clean_frame, 99999, "car", 250, 250, frame_count=201, bbox=bbox3)

        # Xe 3 đến sau KHÔNG ĐƯỢC gán ID 85460 của nhóm trước đó!
        mapped_new = pm.track_id_map.get(99999, 99999)
        self.assertEqual(mapped_new, 99999, "Xe đến sau một lúc phải giữ ID mới của chính nó, không bị gán ID cũ!")
        self.assertIn(99999, pm.waiting_vehicles)
        self.assertNotIn(85460, pm.waiting_vehicles)

    def test_06_center_only_zone_detection(self):
        """
        Kiểm tra: logic phát hiện xe trong vùng CHỈ DÙNG TÂM (cx, cy).
        - Nếu tâm (cx, cy) nằm ngoài polygon, dù góc/đuôi xe có thò vào trong thì VẪN LÀ NGOÀI VÙNG.
        - Nếu tâm (cx, cy) nằm trong polygon, xe được xác định là TRONG VÙNG.
        """
        pm = ParkingManager(None, None)
        pm.setup_detection(fps=30.0)
        # Vùng cấm từ x=200 đến x=400, y=200 đến y=400
        poly = np.array([[200, 200], [400, 200], [400, 400], [200, 400]], dtype=np.int32)
        pm.set_parking_zones([{"polygon": poly, "duration": 30, "name": "Vùng Cấm"}])

        frame = np.zeros((600, 800, 3), dtype=np.uint8)

        # Trường hợp 1: Xe có tâm (150, 300) NẰM NGOÀI vùng, nhưng mũi/góc xe (220, 350) thò vào trong vùng
        # Bbox: x1=100, y1=250, x2=220, y2=350 -> cx=160, cy=300 (ngoài vùng)
        cx_out, cy_out = 160, 300
        bbox_edge_in = (100, 250, 220, 350)
        lbl, color = pm.process_vehicle(frame, frame, 111, "car", cx_out, cy_out, frame_count=1, bbox=bbox_edge_in)
        self.assertNotIn(111, pm.waiting_vehicles, "Tâm nằm ngoài vùng thì không được coi là trong vùng cấm!")

        # Trường hợp 2: Xe có tâm (250, 300) NẰM TRONG vùng cấm
        cx_in, cy_in = 250, 300
        bbox_in = (200, 250, 300, 350)
        pm.process_vehicle(frame, frame, 222, "car", cx_in, cy_in, frame_count=1, bbox=bbox_in)
        pm.process_vehicle(frame, frame, 222, "car", cx_in, cy_in, frame_count=2, bbox=bbox_in)
        self.assertIn(222, pm.waiting_vehicles, "Tâm nằm trong vùng thì được tính là trong vùng cấm!")

    def test_07_stationary_stabilizer_short_vs_long_gap(self):
        """Kiểm tra StationaryVehicleStabilizer: mất dấu ngắn giữ ID, mất dấu lâu tạo ID mới."""
        stab = StationaryVehicleStabilizer(iou_thresh=0.35, iom_thresh=0.50, max_ghost_frames=75)
        bbox = (100, 100, 200, 200)
        center = (150, 150)

        cid1 = stab.get_canonical_id(10, bbox, center, frame_index=1)
        self.assertEqual(cid1, 10)

        # Mất dấu ngắn 30 frames (< 75 frames ~ 1s) -> Xe xuất hiện lại tại cùng vị trí -> Giữ nguyên ID 10
        new_bbox = (101, 100, 201, 200)
        new_center = (151, 150)
        cid2 = stab.get_canonical_id(88, new_bbox, new_center, frame_index=31)
        self.assertEqual(cid2, 10, "Mất dấu ngắn thì giữ nguyên canonical ID cũ")

        # Quá hạn max_ghost_frames (120 frames sau) -> Dọn dẹp tracks
        stab.cleanup_old_tracks(160)
        self.assertNotIn(10, stab.stationary_tracks)

        # Xe mới (ID 99) đến sau tại cùng vị trí đỗ
        cid3 = stab.get_canonical_id(99, (100, 100, 200, 200), (150, 150), frame_index=161)
        self.assertEqual(cid3, 99, "Xe đến sau một lúc phải nhận ID mới của chính nó, không nối vào ID xe cũ!")


if __name__ == "__main__":
    unittest.main()
