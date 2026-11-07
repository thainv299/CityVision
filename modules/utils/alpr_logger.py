import os
import csv
import time
import cv2
from datetime import datetime


def _levenshtein(s1: str, s2: str) -> int:
    """Tính khoảng cách Levenshtein giữa 2 chuỗi để phát hiện OCR nhầm 1 ký tự (ví dụ 0 vs 8, 5 vs S)."""
    if len(s1) < len(s2):
        return _levenshtein(s2, s1)
    if len(s2) == 0:
        return len(s1)
    prev = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        curr = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = prev[j + 1] + 1
            deletions = curr[j] + 1
            substitutions = prev[j] + (c1 != c2)
            curr.append(min(insertions, deletions, substitutions))
        prev = curr
    return prev[-1]


class ALPRLogger:
    def __init__(self, disappear_threshold=1800, db_callback=None, id_camera=0):
        self.logs_dir = "logs"
        self.plates_dir = os.path.join(self.logs_dir, "plates")
        self.csv_path = os.path.join(self.logs_dir, "ALPR_log.csv")
        self.disappear_threshold = disappear_threshold
        self.plate_sessions = {}
        self.logged_v_tracks = set() # Track IDs phương tiện đã được ghi vào DB
        self.recent_logged_plates = [] # List of (plate_text, timestamp) chống duplicate
        self.db_callback = db_callback
        self.id_camera = id_camera
        
        # AsyncIOWorker (inject từ bên ngoài, nếu None sẽ fallback gọi đồng bộ)
        self.io_worker = None
        self.save_to_db = True
        
        os.makedirs(self.plates_dir, exist_ok=True)
        
        if not os.path.exists(self.csv_path):
            with open(self.csv_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(["Time", "Frame", "Plate", "Full_Frame_Image_Path"])

    def process_plate(self, plate_text, current_frame, plate_img, full_frame, plate_coords, v_track_id=None, vehicle_bbox=None):
        now_ts = time.time()

        # 1. BẢO VỆ 1: CHỐNG GHI ĐÚP THEO VEHICLE TRACK ID (CÙNG 1 XE ĐANG DI CHUYỂN TRONG KHUNG HÌNH)
        if v_track_id is not None and v_track_id != -1:
            if v_track_id in self.logged_v_tracks:
                # Xe này đã được ghi nhận trong khung hình rồi -> Bỏ qua, không ghi lần 2
                self.plate_sessions[plate_text] = {"last_seen": current_frame}
                return
            self.logged_v_tracks.add(v_track_id)

        # 2. BẢO VỆ 2: CHỐNG GHI ĐÚP THEO THỜI GIAN & ĐỘ TƯƠNG ĐỒNG BIỂN SỐ (FUZZY SIMILARITY)
        # Dọn dẹp danh sách cũ hơn 30 giây
        self.recent_logged_plates = [(p, t) for p, t in self.recent_logged_plates if now_ts - t < 30.0]

        for prev_p, prev_t in self.recent_logged_plates:
            # Nếu trùng 100% biển số trong vòng 30s -> Bỏ qua
            if prev_p == plate_text:
                self.plate_sessions[plate_text] = {"last_seen": current_frame}
                return
            # Nếu biển số gần giống nhau (sai lệch <= 1 ký tự do OCR nhầm 0/8, 5/S...) trong vòng 15s -> Cùng 1 xe, bỏ qua
            if plate_text != "Không phát hiện biển số xe" and prev_p != "Không phát hiện biển số xe":
                if now_ts - prev_t < 15.0 and _levenshtein(prev_p, plate_text) <= 1:
                    print(f"[ALPRLogger] Phát hiện biển số tương đồng '{plate_text}' với '{prev_p}' trong vòng 15s. Bỏ qua ghi đúp.")
                    self.plate_sessions[plate_text] = {"last_seen": current_frame}
                    return

        is_new_session = False
        if plate_text not in self.plate_sessions:
            is_new_session = True
        else:
            frames_missing = current_frame - self.plate_sessions[plate_text]["last_seen"]
            if frames_missing > self.disappear_threshold:
                is_new_session = True
                
        # Luôn luôn cập nhật thời điểm xuất hiện cuối cùng của xe này
        self.plate_sessions[plate_text] = {"last_seen": current_frame}
        
        if is_new_session:
            self.recent_logged_plates.append((plate_text, now_ts))
            if self.save_to_db:
                self._save_log(plate_text, current_frame, full_frame, plate_coords, vehicle_bbox=vehicle_bbox)
            
    def log_vehicle_without_plate(self, current_frame, full_frame, vehicle_coords):
        """Ghi lại phương tiện ngay cả khi không thấy biển số"""
        if not self.save_to_db:
            return
        plate_text = "Không phát hiện biển số xe"
        now_ts = time.time()
        self.recent_logged_plates = [(p, t) for p, t in self.recent_logged_plates if now_ts - t < 30.0]
        # Không ghi đúp "Không phát hiện biển số xe" liên tục trong 15s
        for prev_p, prev_t in self.recent_logged_plates:
            if prev_p == plate_text and now_ts - prev_t < 15.0:
                return
        self.recent_logged_plates.append((plate_text, now_ts))
        self._save_log(plate_text, current_frame, full_frame, vehicle_coords)

    def _save_log(self, plate_text, current_frame, full_frame, plate_coords, vehicle_bbox=None):
        now = datetime.now()
        timestamp = now.strftime("%Y%m%d_%H%M%S")
        year = now.strftime("%Y")
        month = now.strftime("%m")
        day = now.strftime("%d")
        
        # Tạo cấu trúc thư mục theo ngày: logs/plates/YYYY/MM/DD/
        date_dir = os.path.join(self.plates_dir, year, month, day)
        os.makedirs(date_dir, exist_ok=True)
        
        # Tạo tên file hợp lệ (tránh dấu tiếng Việt/khoảng trắng)
        safe_text = "NoPlate" if plate_text == "Không phát hiện biển số xe" else plate_text
        img_name = f"{safe_text}_{timestamp}_{current_frame}.jpg"
        img_path = os.path.join(date_dir, img_name)
        
        evidence_frame = full_frame.copy()
        h, w = full_frame.shape[:2]
        f_thick = max(1, int(round(1.5 * (w / 1280))))
        
        # Vẽ bounding box của phương tiện (màu xanh lá) nếu có
        if vehicle_bbox:
            vx1, vy1, vx2, vy2 = vehicle_bbox
            cv2.rectangle(evidence_frame, (vx1, vy1), (vx2, vy2), (0, 255, 0), f_thick)
            
        x1, y1, x2, y2 = plate_coords
        cv2.rectangle(evidence_frame, (x1, y1), (x2, y2), (0, 0, 255), f_thick)
        
        # Vẽ text biển số ở góc trên cùng bên trái màn hình (top-left) với nền đỏ chữ trắng
        f_scale_text = max(0.6, 0.9 * (w / 1280))
        # OpenCV cv2.putText không hỗ trợ ký tự UTF-8 có dấu (sẽ bị lỗi dấu ??) -> dùng text không dấu
        display_plate = "KHONG PHAT HIEN BIEN SO" if plate_text == "Không phát hiện biển số xe" else plate_text
        label_text_str = f"BIEN SO: {display_plate}"
        (tw, th), baseline = cv2.getTextSize(label_text_str, cv2.FONT_HERSHEY_SIMPLEX, f_scale_text, f_thick)
        pad = 6
        cv2.rectangle(evidence_frame, (10, 10), (10 + tw + pad * 2, 10 + th + pad * 2 + baseline), (0, 0, 200), -1)
        cv2.putText(evidence_frame, label_text_str, (10 + pad, 10 + th + pad),
                    cv2.FONT_HERSHEY_SIMPLEX, f_scale_text, (255, 255, 255), f_thick, cv2.LINE_AA)
        
        # Đường dẫn web (dùng dấu gạch chéo)
        web_path = img_path.replace(os.sep, "/")
        time_str = now.strftime("%Y-%m-%d %H:%M:%S")
        csv_row = [time_str, current_frame, plate_text, web_path]
        
        # Lưu dữ liệu JSON
        json_path = os.path.join(date_dir, f"{safe_text}_{timestamp}_{current_frame}_meta.json")
        meta = {
            "plate": "No plate detected" if plate_text == "Không phát hiện biển số xe" else plate_text,
            "timestamp": time_str,
            "bbox": vehicle_bbox if vehicle_bbox else plate_coords
        }
        
        if self.io_worker is not None:
            self.io_worker.enqueue_save_image(img_path, evidence_frame)
            self.io_worker.enqueue_csv_append(self.csv_path, csv_row)
            try:
                import json
                with open(json_path, 'w', encoding='utf-8') as jf:
                    json.dump(meta, jf, indent=4)
            except:
                pass
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
            try:
                import json
                with open(json_path, 'w', encoding='utf-8') as jf:
                    json.dump(meta, jf, indent=4)
            except:
                pass
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
