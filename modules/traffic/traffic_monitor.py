import numpy as np
import cv2

# --- NGƯỠNG CẢNH BÁO GIAO THÔNG (CONGESTION THRESHOLDS) ---
CONG_COUNT_THR = 30              # Cấp 1: Tổng xe + người >= 30 để coi là "Đông đúc L1"
CONG_SPEED_THR = 10.0            # Cấp 3: Vận tốc di chuyển tối đa (px/s) để bị coi là "Tắc nghẽn L3"
MAX_VEHICLE_AREA_RATIO = 0.3     # Bỏ qua những hộp Box nhiễu có diện tích lớn hơn 30% vùng giám sát (Tránh lỗi YOLO)
HYSTERESIS_DROP_RATIO = 0.2      # Tỷ lệ sụt giảm (20%) so với ngưỡng ban đầu để tụt mức (chống nhảy cóc)

class TrafficMonitor:
    def __init__(self, roi_polygon=None, congestion_threshold=35.0):
        self.roi_polygon = roi_polygon
        self.congestion_threshold = float(congestion_threshold)
        self.roi_area = cv2.contourArea(np.array(self.roi_polygon)) if self.roi_polygon is not None else 0.0
        self.track_history = {}
        self.vehicle_count = 0
        self.people_count = 0
        self.current_ids_in_roi = []
        
        # Biến mới để lưu danh sách Bounding Box thay vì cộng dồn diện tích
        self.current_bboxes = [] 
        self.last_occupancy = 0.0
        self.current_level = 0 # Lưu mức trạng thái gần nhất để áp dụng Hysteresis
        
        # Cache cho Masking (Tối ưu hiệu năng)
        self._cached_roi_mask = None
        self._cached_roi_pixel_area = 0
        
    def reset_counters(self):
        self.vehicle_count = 0
        self.people_count = 0
        self.current_ids_in_roi = []
        self.current_bboxes = [] # Reset list - LƯU TRỮ BOX CỦA CẢ XE VÀ NGƯỜI

    def log_person(self, bbox=None):
        """Log người → thêm bounding box vào list (cho tính occupancy)
        
        Args:
            bbox: tuple (x1, y1, x2, y2) của bounding box person
        """
        self.people_count += 1
        if bbox is not None:
            # Lọc nhiễu: Bỏ qua bounding box lớn dị thường (lỗi YOLO) > 30% diện tích ROI
            if self.roi_area > 0:
                area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
                if area <= (self.roi_area * MAX_VEHICLE_AREA_RATIO):
                    self.current_bboxes.append(bbox)
            else:
                self.current_bboxes.append(bbox)

    def log_vehicle(self, track_id, cx, cy, current_time, bbox=None):
        """bbox truyền vào dưới dạng tuple (x1, y1, x2, y2)"""
        self.vehicle_count += 1
        if bbox is not None:
            # Lọc nhiễu: Bỏ qua bounding box lớn dị thường (lỗi YOLO) > 30% diện tích ROI
            if self.roi_area > 0:
                area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
                if area <= (self.roi_area * MAX_VEHICLE_AREA_RATIO):
                    self.current_bboxes.append(bbox)
            else:
                self.current_bboxes.append(bbox)
            
        if track_id != -1:
            self.current_ids_in_roi.append(track_id)
            if track_id not in self.track_history:
                self.track_history[track_id] = []
            # Tránh ghi trùng vị trí khi frame skip (reuse kết quả cũ)
            # Nếu vị trí giống hệt entry cuối → không thêm (tránh kéo speed về 0)
            history = self.track_history[track_id]
            if not history or (history[-1][0] != cx or history[-1][1] != cy):
                history.append((cx, cy, current_time))
            self.track_history[track_id] = [p for p in history if current_time - p[2] <= 2.0]

    def calculate_speed_and_status(self, current_time, frame_shape):
        """Cần truyền thêm frame.shape (Kích thước video) để tạo Mask"""
        # 1. TÍNH VẬN TỐC
        total_speed = 0.0
        valid_speed_count = 0
        for tid in list(self.track_history.keys()):
            if tid not in self.current_ids_in_roi:
                if len(self.track_history[tid]) > 0 and (current_time - self.track_history[tid][-1][2]) > 1.0:
                    del self.track_history[tid]
                continue
            points = self.track_history[tid]
            if len(points) >= 2:
                dt = points[-1][2] - points[0][2]
                if dt > 0.2: 
                    speed = np.sqrt((points[-1][0]-points[0][0])**2 + (points[-1][1]-points[0][1])**2) / dt 
                    total_speed += speed
                    valid_speed_count += 1

        avg_speed = total_speed / valid_speed_count if valid_speed_count > 0 else 0.0

        # 2. TÍNH % DIỆN TÍCH BẰNG MASKING (Chống Overlap & Tràn viền)
        occupancy_percent = 0.0
        if self.roi_polygon is not None and len(self.current_bboxes) > 0 and len(frame_shape) >= 2:
            h, w = frame_shape[:2]
            
            # Khởi tạo Mask ROI một lần duy nhất (Cache)
            if self._cached_roi_mask is None or self._cached_roi_mask.shape != (h, w):
                self._cached_roi_mask = np.zeros((h, w), dtype=np.uint8)
                cv2.fillPoly(self._cached_roi_mask, [np.array(self.roi_polygon, dtype=np.int32)], 255)
                self._cached_roi_pixel_area = cv2.countNonZero(self._cached_roi_mask)
            
            # Tạo mask cho các phương tiện hiện tại
            vehicles_mask = np.zeros((h, w), dtype=np.uint8)
            for (x1, y1, x2, y2) in self.current_bboxes:
                x1, y1, x2, y2 = int(max(0, x1)), int(max(0, y1)), int(min(w, x2)), int(min(h, y2))
                cv2.rectangle(vehicles_mask, (x1, y1), (x2, y2), 255, -1)
                
            # Giao 2 vùng lại (Chỉ lấy phần xe nằm TRONG ROI)
            overlap_mask = cv2.bitwise_and(vehicles_mask, self._cached_roi_mask)
            
            # Đếm số pixel màu trắng
            occupied_pixel_area = cv2.countNonZero(overlap_mask)
            
            if self._cached_roi_pixel_area > 0:
                occupancy_percent = (occupied_pixel_area / self._cached_roi_pixel_area) * 100.0

        self.last_occupancy = occupancy_percent # Lưu lại để vẽ lên màn hình

        # 3. ĐÁNH GIÁ MỨC ĐỘ VỚI HYSTERESIS (TIÊN QUYẾT: PHẢI KÍCH HOẠT MỨC 1 TRƯỚC)
        total_count = self.vehicle_count + self.people_count

        # Các ngưỡng tăng bậc (Upward Thresholds)
        l1_count_up = float(CONG_COUNT_THR)                              # 30 xe + người
        l2_occ_up = float(self.congestion_threshold)                     # % diện tích cài đặt (ví dụ 35%)
        l3_occ_extreme_up = l2_occ_up + 30.0                             # Ngưỡng diện tích cực hạn (ví dụ 35% + 30% = 65%)
        l3_speed_up = float(CONG_SPEED_THR)                              # 10.0 px/s

        # Các ngưỡng hạ bậc (Downward Thresholds với 20% Hysteresis margin)
        l1_count_down = l1_count_up * (1.0 - HYSTERESIS_DROP_RATIO)      # 30 * 0.8 = 24
        l2_occ_down = l2_occ_up * (1.0 - HYSTERESIS_DROP_RATIO)          # 35% * 0.8 = 28%
        l3_occ_extreme_down = l3_occ_extreme_up * (1.0 - HYSTERESIS_DROP_RATIO) # 65% * 0.8 = 52%
        l3_speed_down = l3_speed_up * 1.3                                # 13.0 px/s (hồi phục vận tốc)

        prev_level = self.current_level

        # --- BƯỚC 1: ĐIỀU KIỆN TIÊN QUYẾT: PHẢI ĐỦ SỐ LƯỢNG XE/NGƯỜI KÍCH HOẠT MỨC 1 TRƯỚC ---
        # Nếu đang ở Mức 0: Phải có tổng xe + người >= 30 mới bắt đầu kích hoạt cảnh báo đông đúc
        # Nếu đang ở Mức >= 1: Phải duy trì tổng xe + người >= 24 (Hysteresis) để không bị tụt về Mức 0
        min_count_needed = l1_count_down if prev_level > 0 else l1_count_up

        if total_count < min_count_needed:
            # Chưa đủ số lượng xe/người để kích hoạt Mức 1 -> Thông thoáng (Mức 0)
            new_level = 0
        else:
            # --- BƯỚC 2: ĐÃ ĐẠT ĐIỀU KIỆN MỨC 1 -> XÉT TIẾP % DIỆN TÍCH CHO MỨC 2 & MỨC 3 ---
            cond_l3_extreme = (occupancy_percent >= l3_occ_extreme_up)
            cond_l3_speed = (occupancy_percent >= l2_occ_up) and (avg_speed <= l3_speed_up)
            is_l3_candidate = cond_l3_extreme or cond_l3_speed
            is_l2_candidate = (occupancy_percent >= l2_occ_up)

            if prev_level == 3:
                # Đang ở Mức 3: Xét điều kiện duy trì Mức 3
                still_l3_extreme = (occupancy_percent >= l3_occ_extreme_down)
                still_l3_speed = (occupancy_percent >= l2_occ_down) and (avg_speed <= l3_speed_down)
                if still_l3_extreme or still_l3_speed:
                    new_level = 3
                elif occupancy_percent >= l2_occ_down:
                    new_level = 2
                else:
                    new_level = 1

            elif prev_level == 2:
                # Đang ở Mức 2: Có thể vọt lên Mức 3, hoặc giữ Mức 2, hoặc tụt về Mức 1
                if is_l3_candidate:
                    new_level = 3
                elif occupancy_percent >= l2_occ_down:
                    new_level = 2
                else:
                    new_level = 1

            else:
                # Đang ở Mức 1 hoặc vừa từ Mức 0 chuyển lên (đã thỏa total_count >= 30)
                if is_l3_candidate:
                    new_level = 3
                elif is_l2_candidate:
                    new_level = 2
                else:
                    new_level = 1

        self.current_level = new_level
        traffic_level = new_level

        status_text, status_color = self.get_status_for_level(traffic_level, avg_speed)
        return avg_speed, status_text, status_color, traffic_level

    def get_status_for_level(self, level, avg_speed=0.0):
        """Chuyển đổi từ level số sang text và màu sắc hiển thị"""
        total = self.vehicle_count + self.people_count
        if level == 1:
            return f"Trang thai: Dong duc (MUC 1) - {total} phuong tien & nguoi", (0, 165, 255)
        elif level == 2:
            return f"Trang thai: Rat dong (MUC 2) - {self.last_occupancy:.1f}% dien tich", (0, 100, 255)
        elif level == 3:
            return f"Trang thai: TAC NGHEN (MUC 3) - {self.last_occupancy:.1f}% | {avg_speed:.1f} px/s", (0, 0, 255)
        else:
            return "Trang thai: Thong thoang (MUC 0)", (0, 255, 0)

    def draw_status(self, frame, avg_speed, status_text, status_color, f_scale=0.7, f_thick=2, is_admin=False):
        # Tính toán offset dọc dựa trên font_scale để các dòng không bị đè lên nhau trên 4K
        line_height = int(40 * (f_scale / 0.7))
        start_y = int(40 * (f_scale / 0.7))
        
        if is_admin:
            texts = [
                (f"Vehicles: {self.vehicle_count} | People: {self.people_count} (Total: {self.vehicle_count + self.people_count})", (255, 255, 0), f_scale),
                (f"Occupancy: {self.last_occupancy:.1f}% (Threshold: {self.congestion_threshold:.0f}%)", (255, 255, 0), f_scale),
                (f"Avg Speed: {int(avg_speed)} px/s", (255, 255, 0), f_scale),
                (status_text, status_color, f_scale * 1.1)
            ]
        else:
            texts = [
                (f"Vehicles: {self.vehicle_count} | People: {self.people_count}", (255, 255, 0), f_scale),
                (status_text, status_color, f_scale * 1.1)
            ]

        for i, (txt, color, scale) in enumerate(texts):
            curr_y = start_y + i * line_height
            # Lấy kích thước text để vẽ nền
            (tw, th), baseline = cv2.getTextSize(txt, cv2.FONT_HERSHEY_SIMPLEX, scale, f_thick)
            
            # Vẽ nền đen bán trong suốt
            sub_img = frame[curr_y - th - 5 : curr_y + baseline + 5, 30 - 5 : 30 + tw + 5]
            if sub_img.size > 0:
                black_rect = np.zeros(sub_img.shape, dtype=np.uint8)
                res = cv2.addWeighted(sub_img, 0.5, black_rect, 0.5, 1.0)
                frame[curr_y - th - 5 : curr_y + baseline + 5, 30 - 5 : 30 + tw + 5] = res
            
            # Vẽ chữ
            cv2.putText(frame, txt, (30, curr_y), cv2.FONT_HERSHEY_SIMPLEX, scale, color, f_thick)
