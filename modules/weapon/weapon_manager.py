import os
import time
import math
import cv2
import datetime
import threading
from modules.utils.common_utils import ensure_dir
from modules.utils.telegram_bot import send_telegram_image

WEAPON_LABELS = {"knife", "pistol", "sword"}
WEAPON_NAME_MAP = {
    "knife": "DAO",
    "pistol": "SÚNG NGẮN",
    "sword": "KIẾM / DAO DÀI"
}

class WeaponManager:
    def __init__(self, camera_id: int = 0, camera_name: str = "Camera"):
        self.camera_id = camera_id
        self.camera_name = camera_name
        self.frame_counts = {}  # {key: count}
        self.last_seen = {}      # {key: timestamp}
        self.alerted_keys = {}   # {key: timestamp}
        self.lock = threading.Lock()
        self.save_dir = "logs/weapons"
        ensure_dir(self.save_dir)

    def _get_spatial_key(self, track_id: int, label: str, cx: int, cy: int) -> str:
        """Tạo khoá định danh cho vết vũ khí dựa trên track_id hoặc tọa độ không gian (bán kính 60px)"""
        if track_id != -1:
            return f"track_{track_id}_{label}"
        
        # Nếu không có track_id, dùng lưới tọa độ ô 60px
        grid_x = int(cx // 60)
        grid_y = int(cy // 60)
        return f"pos_{grid_x}_{grid_y}_{label}"

    def process_weapon(
        self,
        frame,
        clean_frame,
        track_id: int,
        label: str,
        confidence: float,
        bbox,
        camera_id: int = None,
        camera_name: str = None,
        telegram_enabled: bool = True
    ):
        """
        Xử lý thông tin phát hiện vũ khí ở mỗi khung hình.
        Chỉ khi phát hiện đủ 5 frame liên tục/tích lũy mới ghi nhận sự kiện & cảnh báo.
        """
        if label not in WEAPON_LABELS:
            return None, None

        if camera_id is not None:
            self.camera_id = camera_id
        if camera_name is not None:
            self.camera_name = camera_name

        now = time.time()
        x1, y1, x2, y2 = bbox
        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
        key = self._get_spatial_key(track_id, label, cx, cy)

        with self.lock:
            # 1. Dọn dẹp các vết vũ khí không còn xuất hiện (> 3.0 giây)
            stale_keys = [k for k, last_t in self.last_seen.items() if now - last_t > 3.0]
            for sk in stale_keys:
                del self.last_seen[sk]
                if sk in self.frame_counts:
                    del self.frame_counts[sk]

            # 2. Cập nhật đếm frame cho vết hiện tại
            self.last_seen[key] = now
            current_count = self.frame_counts.get(key, 0) + 1
            self.frame_counts[key] = current_count

            # Bounding box hiển thị trên live stream (Đỏ nổi bật)
            box_color = (0, 0, 255) # RGB Red
            display_label = f"⚠️ VŨ KHÍ: {WEAPON_NAME_MAP.get(label, label.upper())} ({int(confidence * 100)}%) [{current_count}/5]"

            # 3. Kiểm tra xem đã đủ 5 frame chưa và đã hết thời gian debounce (45s) chưa
            last_alert_time = self.alerted_keys.get(key, 0)
            is_debounced = (now - last_alert_time) < 45.0

            if current_count >= 5 and not is_debounced:
                self.alerted_keys[key] = now
                
                # Tạo ảnh bằng chứng với Bounding Box ĐỎ
                evidence_img = clean_frame.copy()
                h, w = evidence_img.shape[:2]
                f_scale = max(0.6, min(w, h) / 1000.0)
                f_thick = max(2, int(f_scale * 2))

                # Vẽ khung màu đỏ dày xung quanh vũ khí
                cv2.rectangle(evidence_img, (x1, y1), (x2, y2), (0, 0, 255), f_thick + 2)
                
                # Vẽ nhãn cảnh báo đỏ
                txt = f"⚠️ PHÁT HIỆN VŨ KHÍ: {WEAPON_NAME_MAP.get(label, label.upper())} ({int(confidence * 100)}%)"
                (tw, th), bl = cv2.getTextSize(txt, cv2.FONT_HERSHEY_SIMPLEX, f_scale, f_thick)
                ty = max(th + 10, y1 - 10)
                cv2.rectangle(evidence_img, (x1, ty - th - 8), (x1 + tw + 8, ty + bl + 4), (0, 0, 255), -1)
                cv2.putText(evidence_img, txt, (x1 + 4, ty), cv2.FONT_HERSHEY_SIMPLEX, f_scale, (255, 255, 255), f_thick)

                # Lưu ảnh bằng chứng vào thư mục logs/weapons/
                ts_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                filename = f"weapon_{self.camera_id}_{label}_{ts_str}.jpg"
                rel_path = os.path.join(self.save_dir, filename).replace("\\", "/")
                cv2.imwrite(rel_path, evidence_img)

                # Lưu sự kiện vào CSDL
                try:
                    from database.sqlite_db import log_weapon_detection
                    log_weapon_detection(
                        camera_id=self.camera_id,
                        loai_vu_khi=label,
                        thoi_gian_phat_hien=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        do_chinh_xac=float(confidence),
                        frame_path=rel_path
                    )
                except Exception as e:
                    print(f"[WeaponManager] Lỗi lưu DB phát hiện vũ khí: {e}")

                # Gửi cảnh báo Telegram khẩn cấp
                if telegram_enabled:
                    caption = (
                        f"🚨 <b>CẢNH BÁO NGUY HIỂM: PHÁT HIỆN VŨ KHÍ!</b>\n"
                        f"📹 Camera: <b>{self.camera_name}</b> (ID: {self.camera_id})\n"
                        f"🗡️ Loại vũ khí: <b>{WEAPON_NAME_MAP.get(label, label.upper())}</b>\n"
                        f"🎯 Độ chính xác: <b>{int(confidence * 100)}%</b>\n"
                        f"⏰ Thời gian: <b>{datetime.datetime.now().strftime('%d/%m/%Y %H:%M:%S')}</b>"
                    )
                    threading.Thread(
                        target=self._send_telegram_async,
                        args=(rel_path, caption),
                        daemon=True
                    ).start()

            return display_label, box_color

    def _send_telegram_async(self, image_path: str, caption: str):
        try:
            send_telegram_image(image_path, caption)
            print(f"[WeaponManager] Đã gửi cảnh báo vũ khí qua Telegram: {image_path}")
        except Exception as e:
            print(f"[WeaponManager] Lỗi gửi Telegram cảnh báo vũ khí: {e}")
