import os
import csv
import cv2
from datetime import datetime

class ALPRLogger:
    def __init__(self, disappear_threshold=1800, db_callback=None, id_camera=0):
        self.logs_dir = "logs"
        self.plates_dir = os.path.join(self.logs_dir, "plates")
        self.csv_path = os.path.join(self.logs_dir, "ALPR_log.csv")
        self.disappear_threshold = disappear_threshold
        self.plate_sessions = {}
        self.logged_v_tracks = set() # Track IDs already logged to DB
        self.db_callback = db_callback
        self.id_camera = id_camera
        
        # AsyncIOWorker (inject từ bên ngoài, nếu None sẽ fallback gọi đồng bộ)
        self.io_worker = None
        
        os.makedirs(self.plates_dir, exist_ok=True)
        
        if not os.path.exists(self.csv_path):
            with open(self.csv_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(["Time", "Frame", "Plate", "Full_Frame_Image_Path"])

    def process_plate(self, plate_text, current_frame, plate_img, full_frame, plate_coords, v_track_id=None):
        is_new_session = False
        
        if v_track_id is not None:
            self.logged_v_tracks.add(v_track_id)

        if plate_text not in self.plate_sessions:
            is_new_session = True
        else:
            frames_missing = current_frame - self.plate_sessions[plate_text]["last_seen"]
            if frames_missing > self.disappear_threshold:
                is_new_session = True
                
        # Luôn luôn cập nhật thời điểm xuất hiện cuối cùng của xe này
        self.plate_sessions[plate_text] = {"last_seen": current_frame}
        
        if is_new_session:
            self._save_log(plate_text, current_frame, full_frame, plate_coords)
            
    def log_vehicle_without_plate(self, current_frame, full_frame, vehicle_coords):
        """Ghi lại phương tiện ngay cả khi không thấy biển số"""
        plate_text = "Không phát hiện biển số xe"
        self._save_log(plate_text, current_frame, full_frame, vehicle_coords)

    def _save_log(self, plate_text, current_frame, full_frame, plate_coords):
        now = datetime.now()
        timestamp = now.strftime("%Y%m%d_%H%M%S")
        year = now.strftime("%Y")
        month = now.strftime("%m")
        day = now.strftime("%d")
        
        # Create date-based folder structure: logs/plates/YYYY/MM/DD/
        date_dir = os.path.join(self.plates_dir, year, month, day)
        os.makedirs(date_dir, exist_ok=True)
        
        # Tạo tên file hợp lệ (tránh dấu tiếng Việt/khoảng trắng)
        safe_text = "NoPlate" if plate_text == "Không phát hiện biển số xe" else plate_text
        img_name = f"{safe_text}_{timestamp}_{current_frame}.jpg"
        img_path = os.path.join(date_dir, img_name)
        
        evidence_frame = full_frame.copy()
        h, w = full_frame.shape[:2]
        f_thick = max(1, int(round(2 * (w / 1280))))
        x1, y1, x2, y2 = plate_coords
        cv2.rectangle(evidence_frame, (x1, y1), (x2, y2), (0, 0, 255), f_thick)
        
        # Vẽ text tiếng Việt bằng PIL
        try:
            from PIL import Image, ImageDraw, ImageFont
            import numpy as np
            img_pil = Image.fromarray(cv2.cvtColor(evidence_frame, cv2.COLOR_BGR2RGB))
            draw = ImageDraw.Draw(img_pil)
            
            font_size = int(30 * (w / 1280))
            try:
                # Arial hỗ trợ Unicode tốt trên Windows
                font = ImageFont.truetype("arial.ttf", font_size)
            except:
                font = ImageFont.load_default()
            
            draw.text((x1, max(0, y1 - font_size - 10)), plate_text, font=font, fill=(255, 0, 0))
            evidence_frame = cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2BGR)
        except Exception as e:
            print(f"[ALPR Logger] Lỗi vẽ font tiếng Việt: {e}")
            cv2.putText(evidence_frame, "No Plate", (x1, max(30, y1 - 10)), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8 * (w/1280), (0, 0, 255), f_thick)
        
        # Web path (forward slashes)
        web_path = img_path.replace(os.sep, "/")
        time_str = now.strftime("%Y-%m-%d %H:%M:%S")
        csv_row = [time_str, current_frame, plate_text, web_path]
        
        if self.io_worker is not None:
            self.io_worker.enqueue_save_image(img_path, evidence_frame)
            self.io_worker.enqueue_csv_append(self.csv_path, csv_row)
            if self.db_callback:
                self.io_worker.enqueue_db_write(
                    self.db_callback,
                    kwargs={
                        "license_plate": plate_text,
                        "detection_count": 1,
                        "avg_confidence": 0.0,
                        "image_paths": web_path,
                        "camera_id": self.id_camera,
                    }
                )
        else:
            cv2.imwrite(img_path, evidence_frame)
            with open(self.csv_path, 'a', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(csv_row)
            if self.db_callback:
                try:
                    self.db_callback(
                        license_plate=plate_text,
                        detection_count=1,
                        avg_confidence=0.0,
                        image_paths=web_path,
                        camera_id=self.id_camera
                    )
                except Exception as e:
                    print(f"[ALPR Database] Lỗi khi ghi vào database: {e}")

