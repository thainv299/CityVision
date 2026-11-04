import datetime
import time
import os
import cv2
from backend.core.config import DATABASE_PATH
from modules.utils.interactive_telegram_bot import send_alert_with_button, send_clear_alert

class TrafficAlertManager:
    """
    Quản lý cảnh báo tắc nghẽn giao thông thông minh:
    - Debounce 15 giây chống nhiễu tức thời
    - Quản lý cảnh báo theo Phiên (Incident / Session State):
        + Trong cùng 1 phiên, chỉ cảnh báo 1 lần cho mỗi cấp độ.
        + Dao động lùi (ví dụ 2 -> 1 -> 2) KHÔNG gửi lại Mức 2.
        + Chỉ gửi ngay khi mức độ tăng vọt nghiêm trọng hơn (ví dụ 2 -> 3).
    - Nhắc nhở định kỳ sau 5 phút (300 giây) nếu tình trạng >= Mức 2 kéo dài liên tục và chưa bấm Xác nhận.
    - Tự động gửi thông báo "Đã thông thoáng trở lại" khi đường vắng liên tục >= 30 giây để khép lại phiên.
    """
    def __init__(self):
        # Cấu hình thời gian (giây)
        self.DEBOUNCE_SECONDS = 15.0       # Cần 15s trạng thái ổn định để xác nhận mức mới
        self.TRUE_CLEAR_SECONDS = 30.0      # Đường trống liên tục 30s để khép lại phiên hoàn toàn
        self.REMINDER_INTERVAL = 300.0      # Nhắc lại sau 5 phút (300s) nếu vẫn đông và chưa xác nhận
        self.SNOOZE_AFTER_ACK = 600.0       # Tạm ngưng 10 phút sau khi người dùng bấm Xác nhận trên Telegram
        
        # Các biến quản lý Debounce
        self.pending_level = 0
        self.pending_start_time = 0.0
        self.confirmed_level = 0
        
        # Quản lý theo Phiên (Session / Incident State)
        self.session_active = False         # Cờ đánh dấu đang có 1 sự cố đông đúc diễn ra
        self.session_max_level = 0          # Mức cao nhất đã phát cảnh báo trong phiên hiện tại
        self.last_alert_time = 0.0          # Thời điểm gửi tin nhắn cảnh báo gần nhất
        self.clear_start_time = 0.0         # Thời điểm bắt đầu vắng đường (Mức 0)
        self.is_acknowledged = False        # Người dùng đã bấm Xác nhận chưa
        self.snooze_until = 0.0             # Thời điểm hết hạn tạm ngưng
        
        # AsyncIOWorker (inject từ bên ngoài)
        self.io_worker = None
        
        # Quyền lưu DB và camera ID
        self.save_to_db = True
        self.camera_id = 1
        self.tts_enabled = True
        
        os.makedirs("logs", exist_ok=True)

    def update_traffic_state(self, raw_level, clean_frame):
        current_time = time.time()
        
        # --- BƯỚC A: Lọc nhiễu (Debounce Logic 15s) ---
        if raw_level != self.pending_level:
            self.pending_level = raw_level
            self.pending_start_time = current_time
            
        if current_time - self.pending_start_time >= self.DEBOUNCE_SECONDS:
            self.confirmed_level = self.pending_level
            
        # --- BƯỚC B: Xử lý khi đường Thông Thoáng (Mức 0) ---
        if self.confirmed_level == 0:
            if self.clear_start_time == 0.0:
                self.clear_start_time = current_time
            elif current_time - self.clear_start_time >= self.TRUE_CLEAR_SECONDS:
                # Đường thật sự vắng liên tục 30s -> Kết thúc phiên đông đúc!
                if self.session_active:
                    if self.session_max_level >= 2:
                        # Từng phát cảnh báo Mức 2 hoặc Mức 3 -> Gửi thông báo đã thông thoáng
                        self._trigger_clear_notification()
                    print(f"[TrafficAlertManager] Phiên đông đúc (Max Level {self.session_max_level}) đã kết thúc. Giao thông thông thoáng.")
                    self.session_active = False
                    self.session_max_level = 0
                    self.last_alert_time = 0.0
                    self.snooze_until = 0.0
                    self.is_acknowledged = False
            return
        else:
            self.clear_start_time = 0.0 # Đang có xe thì reset bộ đếm vắng đường

        # --- BƯỚC C: Cảnh báo theo Phiên (Incident / Session State) ---
        # Kiểm tra chế độ Ngủ đông (Snooze) khi người dùng đã bấm Xác nhận
        if current_time < self.snooze_until:
            # Nếu mức độ nghiêm trọng hơn mức cao nhất trong phiên (ví dụ nhảy lên Mức 3) -> Vẫn phá snooze để báo khẩn
            if self.confirmed_level <= self.session_max_level:
                return

        if not self.session_active:
            # 1. BẮT ĐẦU PHIÊN ĐÔNG ĐÚC MỚI
            self.session_active = True
            self.session_max_level = self.confirmed_level
            self._trigger_alert(self.confirmed_level, clean_frame, is_reminder=False)
            self.last_alert_time = current_time
            self.is_acknowledged = False
            print(f"[TrafficAlertManager] Khởi động phiên cảnh báo mới: Mức {self.confirmed_level}.")
        else:
            # 2. ĐANG TRONG PHIÊN CẢNH BÁO
            if self.confirmed_level > self.session_max_level:
                # Mức độ nghiêm trọng tăng lên (ví dụ từ Mức 2 vọt lên Mức 3: Tắc nghẽn) -> Báo ngay!
                self.session_max_level = self.confirmed_level
                self._trigger_alert(self.confirmed_level, clean_frame, is_reminder=False)
                self.last_alert_time = current_time
                self.is_acknowledged = False
                print(f"[TrafficAlertManager] Tăng cấp độ nghiêm trọng lên Mức {self.confirmed_level}. Đã phát cảnh báo mới.")
            elif (not self.is_acknowledged) and (self.confirmed_level >= 2) and (current_time - self.last_alert_time >= self.REMINDER_INTERVAL):
                # Giải pháp D: Nếu tình trạng đông đúc (>= Mức 2) kéo dài liên tục > 5 phút và chưa bấm Xác nhận -> Nhắc lại
                self._trigger_alert(self.confirmed_level, clean_frame, is_reminder=True)
                self.last_alert_time = current_time
                print(f"[TrafficAlertManager] Nhắc lại cảnh báo Mức {self.confirmed_level} sau 5 phút do tình trạng vẫn tiếp diễn.")
            else:
                # Dao động lùi hoặc mức bằng mức cũ (ví dụ 2 -> 1 -> 2): BỎ QUA, KHÔNG GỬI LẠI TRÁNH SPAM!
                pass

    def acknowledge_alert(self):
        """User bấm phím A trên desktop"""
        self.snooze_until = time.time() + self.SNOOZE_AFTER_ACK
        self.is_acknowledged = True
        print(f"[INFO] Bấm phím 'A': Đã tạm hoãn cảnh báo 10 phút cho mức <= {self.confirmed_level}.")

    def user_feedback_received(self, acked_level, message_send_time):
        """User bấm nút 'Xác nhận ✅' trên Telegram."""
        self.is_acknowledged = True
        self.snooze_until = time.time() + self.SNOOZE_AFTER_ACK
        print(f"[INFO] Telegram ACK Mức {acked_level}. Tạm dừng nhắc nhở 10 phút (Snooze đến {time.strftime('%H:%M:%S', time.localtime(self.snooze_until))}).")

    def _trigger_alert(self, level, frame, is_reminder=False):
        # 1. Lưu ảnh bằng chứng để gửi Telegram
        img_path = "logs/traffic_alert.jpg"
        cv2.imwrite(img_path, frame)
        
        # 2. Định nghĩa nội dung chi tiết theo mức độ
        messages = {
            1: "Mức độ 1: Giao thông đang Bắt Đầu Đông",
            2: "Mức độ 2: Giao thông đang Rất Đông Đúc",
            3: "Mức độ 3: Tắc nghẽn nghiêm trọng"
        }
        detail = messages.get(level, f"Mức độ {level}")
        prefix = "CẢNH BÁO TIẾP TỤC ⚠️ (Kéo dài > 5 phút): " if is_reminder else "CẢNH BÁO ⚠️: "
        caption = f"{prefix}{detail}"
        
        voice_messages = {
            1: None,
            2: "Khu vực cổng trường đang rất đông, kính mời phụ huynh nhanh chóng đón học sinh và di chuyển, tránh gây ùn tắc",
            3: "Khu vực cổng trường đang ùn tắc, Yêu cầu tất cả phương tiện tuyệt đối không dừng đỗ và nhanh chóng di chuyển"
        }
        speech_text = voice_messages.get(level)
        # 3. Phát âm thanh cảnh báo (áp dụng cho Mức 3)
        if self.tts_enabled and level == 3 and speech_text and (not is_reminder):
            try:
                from modules.utils.tts_announcer import announce
                announce(speech_text)
            except Exception as e:
                print(f"[TrafficAlertManager] Lỗi phát âm thanh TTS: {e}")

        # 4. Gửi thông báo sang Telegram nếu camera đang hoạt động (enable)
        try:
            from backend.database.sqlite_db import is_camera_enabled
            if not is_camera_enabled(self.camera_id):
                print(f"[TrafficAlertManager] Camera {self.camera_id} đang tắt/không hoạt động. Không gửi cảnh báo Telegram.")
                return
        except Exception as e:
            print(f"[TrafficAlertManager] Lỗi kiểm tra trạng thái hoạt động của camera: {e}")

        send_alert_with_button(img_path, caption, level)

    def _trigger_clear_notification(self):
        """Gửi thông báo nhẹ khi đường đã thông thoáng trở lại"""
        try:
            from backend.database.sqlite_db import is_camera_enabled
            if not is_camera_enabled(self.camera_id):
                return
        except Exception:
            pass
        msg = f"✅ THÔNG BÁO: Giao thông khu vực (Camera {self.camera_id}) đã THÔNG THOÁNG trở lại."
        send_clear_alert(msg)

    # --- CÁC HÀM PHỤ ĐỂ LƯU CHI TIẾT ---
    def save_congestion(self, level, img_path):
        if not self.save_to_db:
            return
        try:
            from backend.database.sqlite_db import log_congestion
            log_congestion(camera_id=self.camera_id, level=level, duong_dan_anh=img_path)
        except Exception as e:
            print("Lỗi lưu tắc nghẽn:", e)

    def save_parking(self, plate_number, img_path):
        if not self.save_to_db:
            return
        try:
            from backend.database.sqlite_db import log_parking_violation
            log_parking_violation(camera_id=self.camera_id, license_plate=plate_number, frame_path=img_path)
        except Exception as e:
            print("Lỗi lưu dừng đỗ:", e)
