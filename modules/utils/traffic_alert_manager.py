import datetime
import time
import os
import cv2
# import pyttsx3
# import threading
from backend.core.config import DATABASE_PATH
from modules.utils.interactive_telegram_bot import send_alert_with_button

class TrafficAlertManager:
    def __init__(self):
        # Cấu hình thời gian đếm ngược tĩnh (giây)
        self.DEBOUNCE_SECONDS = 3.0
        self.TRUE_CLEAR_SECONDS = 5.0 # Số giây đường TRỐNG LIÊN TỤC để được coi là hết kẹt xe hoàn toàn
        self.INTERVAL_UNACK = {1: 300, 2: 60, 3: 30}
        self.SNOOZE_ACK = {1: 900, 2: 600, 3: 300}
        
        # Các biến trạng thái quản lý (Debounce & Tiết chế Cảnh báo)
        self.pending_level = 0
        self.pending_start_time = 0
        self.confirmed_level = 0
        self.last_alert_level = 0
        self.snooze_until = 0
        self.is_acknowledged = False
        self.clear_start_time = 0
        
        # AsyncIOWorker (inject từ bên ngoài, nếu None sẽ fallback gọi đồng bộ)
        self.io_worker = None
        
        # Đảm bảo thư mục lưu log tồn tại
        os.makedirs("logs", exist_ok=True)

    def update_traffic_state(self, raw_level, clean_frame):
        current_time = time.time()
        
        # --- BƯỚC A: Lọc nhiễu (Debounce Logic) ---
        if raw_level != self.pending_level:
            self.pending_level = raw_level
            self.pending_start_time = current_time
            
        if current_time - self.pending_start_time >= self.DEBOUNCE_SECONDS:
            self.confirmed_level = self.pending_level
            
        # --- BƯỚC B: Cảnh báo (Alert Logic) ---
        if self.confirmed_level == 0:
            if self.clear_start_time == 0:
                self.clear_start_time = current_time
            elif current_time - self.clear_start_time >= self.TRUE_CLEAR_SECONDS:
                # Đường thật sự vắng trong liên tục -> Hết kẹt xe dứt điểm!
                # Reset lại toàn bộ bộ nhớ cũ và huỷ bỏ mọi Snooze còn tồn đọng.
                self.last_alert_level = 0
                self.snooze_until = 0
            return
        else:
            self.clear_start_time = 0 # Đang có xe thì reset bộ đếm vắng đường
            
        is_escalation = self.confirmed_level > self.last_alert_level
        timer_expired = current_time >= self.snooze_until
        
        if is_escalation or timer_expired:
            self._trigger_alert(self.confirmed_level, clean_frame)
            self.last_alert_level = self.confirmed_level
            # Kích hoạt trạng thái Un-Acked với thời gian ngắn
            self.snooze_until = current_time + self.INTERVAL_UNACK.get(self.confirmed_level, 60)
            self.is_acknowledged = False # Đặt lại cờ chưa xác nhận khi có cảnh báo mới phát ra

    def acknowledge_alert(self):
        """User pressed 'A' on keyboard"""
        self.snooze_until = time.time() + self.SNOOZE_ACK.get(self.confirmed_level, 300)
        self.is_acknowledged = True
        print(f"[INFO] Bấm phím 'A': Hệ thống tạm chuyển sang chế độ Ngủ đông (Snooze) cho mức độ <= {self.confirmed_level}.")

    def user_feedback_received(self, acked_level, message_send_time):
        """User clicked ACK button on Telegram."""
        # Calculate the snooze end time relative to when the alert was ACTUALLY sent
        snooze_duration = self.SNOOZE_ACK.get(acked_level, 300)
        calculated_snooze_end = message_send_time + snooze_duration
        
        current_time = time.time()
        self.is_acknowledged = True
        
        if calculated_snooze_end > current_time:
            self.snooze_until = max(self.snooze_until, calculated_snooze_end)
            print(f"[INFO] Telegram ACK Mức {acked_level}. Snoozing until {time.strftime('%H:%M:%S', time.localtime(self.snooze_until))}.")
        else:
            print(f"[INFO] Telegram ACK Mức {acked_level}, nhưng {snooze_duration}s đã hết hạn. Không kích hoạt Snooze.")

    def _trigger_alert(self, level, frame):
        # 1. Lưu ảnh để hiển thị
        img_path = "logs/traffic_alert.jpg"
        cv2.imwrite(img_path, frame)
        
        # 2. Định nghĩa nội dung chi tiết theo mức độ
        messages = {
            1: "Mức độ 1: Giao thông đang Bắt Đầu Đông ",
            2: "Mức độ 2: Giao thông đang Rất Đông Đúc",
            3: "Mức độ 3: Tắc nghẽn nghiêm trọng"
        }
        detail = messages.get(level, f"Mức độ {level}")
        caption = f"CẢNH BÁO ⚠️: {detail}"
        
        voice_messages = {
            1: "Giao thông đang bắt đầu đông đúc",
            2: "Giao thông đang rất đông đúc, phụ huynh nhanh chóng di chuyển",
            3: "Giao thông đang tắc nghẽn nghiêm trọng, nhắc lại, phụ huynh nhanh chóng di chuyển"
        }
        speech_text = voice_messages.get(level)
        # if speech_text:
        #     self._speak_alert(speech_text)

        import sqlite3
        from datetime import datetime
        from backend.core.config import DATABASE_PATH 
        
        try:
            conn = sqlite3.connect(DATABASE_PATH) 
            cursor = conn.cursor()
            
            cursor.execute(
                "INSERT INTO violations (type, license_plate, camera_id, image_path, time) VALUES (?, ?, ?, ?, ?)",
                ("Congestion", detail, "Camera 01", img_path, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            )
            conn.commit()
            conn.close()
        except Exception as e:
            print("Lỗi lưu database AI:", e)

        # 4. Gửi thông báo sang Telegram
        send_alert_with_button(img_path, caption, level)
    # --- CÁC HÀM PHỤ ĐỂ LƯU CHI TIẾT ---
    def save_congestion(self, level, img_path):
        messages = {
            1: "Mức độ 1: Giao thông đang Bắt Đầu Đông ",
            2: "Mức độ 2: Giao thông đang Rất Đông Đúc",
            3: "Mức độ 3: Tắc nghẽn nghiêm trọng"
        }
        detail = messages.get(level, f"Mức độ {level}")
        
        try:
            conn = sqlite3.connect(DATABASE_PATH)
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO violations (type, license_plate, camera_id, image_path, time) VALUES (?, ?, ?, ?, ?)",
                ("Congestion", detail, "Camera 01", img_path, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            )
            conn.commit()
            conn.close()
        except Exception as e:
            print("Lỗi lưu tắc nghẽn:", e)

    def save_parking(self, plate_number, img_path):
        try:
            conn = sqlite3.connect(DATABASE_PATH)
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO violations (type, license_plate, camera_id, image_path, time) VALUES (?, ?, ?, ?, ?)",
                ("Parking", plate_number, "Camera 01", img_path, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            )
            conn.commit()
            conn.close()
        except Exception as e:
            print("Lỗi lưu dừng đỗ:", e)
    
    # def _speak_alert(self, text):
    #     """"""
    #     return
    #     def run_tts():
    #         try:
    #             engine = pyttsx3.init()
                
    #             # --- TÌM GIỌNG TIẾNG VIỆT TRONG MÁY ---
    #             voices = engine.getProperty('voices')
    #             vietnamese_voice_id = None
    #             for voice in voices:
    #                 # Tìm giọng nào có chữ "vietnamese" hoặc "vi-vn"
    #                 if "vietnamese" in voice.name.lower() or "vi-vn" in voice.id.lower():
    #                     vietnamese_voice_id = voice.id
    #                     break
                
    #             if vietnamese_voice_id:
    #                 engine.setProperty('voice', vietnamese_voice_id)
    #             else:
    #                 print("[CẢNH BÁO] Máy chưa cài giọng Tiếng Việt, sẽ dùng giọng mặc định.")

    #             engine.setProperty('rate', 150) # Tốc độ nói chậm lại tí cho dễ nghe
    #             engine.say(text)
    #             engine.runAndWait()
    #         except Exception as e:
    #             print(f"[Lỗi Loa] {e}")

    #     threading.Thread(target=run_tts, daemon=True).start()

