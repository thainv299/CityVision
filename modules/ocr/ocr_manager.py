import threading
import queue
import cv2
import numpy as np
import math
from collections import Counter
from . import ocr_processor

class OCRManager:
    def __init__(self, reader, interval=4, vote_threshold=3, max_lost_frames=5, alpr_logger=None):
        self.reader = reader
        self.OCR_INTERVAL = interval
        self.VOTE_THRESHOLD = vote_threshold
        self.MAX_LOST_FRAMES = max_lost_frames
        self.alpr_logger = alpr_logger
        
        self.queue = queue.Queue(maxsize=3)
        self.pending_results = {}
        self.worker_running = False
        
        self.plate_history = {}
        self.plate_confirmed = {}
        self.plate_raw_cache = {}
        self.active_tracks = {}
        self.spatial_memory = {}
        self.last_seen_plate = {}
        self.last_comparison_window = None
        self.vehicle_plates = {} # Maps v_track_id -> confirmed_plate_string

    def start_worker(self):
        if not self.worker_running:
            self.worker_running = True
            threading.Thread(target=self._worker, daemon=True).start()

    def stop_worker(self):
        self.worker_running = False

    def _worker(self):
        """Thread hoạt động ngầm: Lấy ảnh từ queue -> chạy PaddleOCR -> Trả về kết quả"""
        while self.worker_running:
            try:
                track_id, img_crop = self.queue.get(timeout=0.5)
            except queue.Empty:
                continue

            try:
                clean_text, final_text, img_processed, img_plate_color, status_text, dst_w, dst_h = ocr_processor.run_ocr(self.reader, img_crop)
                self.pending_results[track_id] = {
                    'clean_text': clean_text,
                    'final_text': final_text,
                    'img_processed': img_processed,
                    'img_before': img_crop,
                    'dst_w': dst_w,
                    'dst_h': dst_h
                }
            except Exception as e:
                print(f"[OCR Worker] Lỗi: {e}")
            finally:
                self.queue.task_done()

    def cleanup_memory(self, current_time, frame_count):
        """Dọn dẹp rác bộ nhớ (Memory Leak Prevention)"""
        for tid in list(self.last_seen_plate.keys()):
            if current_time - self.last_seen_plate[tid] > 5.0: # Không thấy trong 5 giây thì xóa
                self.plate_history.pop(tid, None)
                self.plate_confirmed.pop(tid, None)
                self.plate_raw_cache.pop(tid, None)
                del self.last_seen_plate[tid]
                self.pending_results.pop(tid, None)
        
        for sid in list(self.spatial_memory.keys()):
            if frame_count - self.spatial_memory[sid][3] > 300: # Xóa vết cũ sau khoảng 10s
                del self.spatial_memory[sid]

    def draw_grace_period_boxes(self, frame, current_plate_ids, drawing_params=None):
        """Vẽ bù các box biển số khi mất khung hình (Grace Period)"""
        # Lấy tham số vẽ linh hoạt
        f_scale, f_thick, f_offset = (0.7, 2, 10)
        if drawing_params:
            f_scale, f_thick, f_offset = drawing_params
        else:
            # Tự tính nếu không truyền vào
            h, w = frame.shape[:2]
            f_scale = max(0.45, 0.55 * (w / 1280))
            f_thick = max(1, int(round(2 * (w / 1280))))
            f_offset = max(10, int(round(15 * (w / 1280))))

        for tid in list(self.active_tracks.keys()):
            if tid not in current_plate_ids:
                self.active_tracks[tid]['missed_frames'] += 1
                if self.active_tracks[tid]['missed_frames'] > self.MAX_LOST_FRAMES:
                    del self.active_tracks[tid]
                else:
                    old_x1, old_y1, old_x2, old_y2 = self.active_tracks[tid]['bbox']
                    if tid in self.plate_confirmed:
                        display_text, color = f"[OK] {self.plate_confirmed[tid]}", (0, 255, 0)
                    else:
                        display_text = (self.plate_history.get(tid) or ["..."])[-1]
                        color = (0, 165, 255)
                    (tw, th), baseline = cv2.getTextSize(display_text, cv2.FONT_HERSHEY_SIMPLEX, f_scale, f_thick)
                    text_y = max(th + 5, old_y1 - f_offset)
                    cv2.rectangle(frame, (old_x1, text_y - th - 5), (old_x1 + tw + 5, text_y + baseline + 2), color, -1)
                    cv2.putText(frame, display_text, (old_x1 + 2, text_y), cv2.FONT_HERSHEY_SIMPLEX, f_scale, (255, 255, 255), f_thick)

    def process_plate(self, frame, clean_frame, track_id, x1, y1, x2, y2, cx, cy, valid_vehicles, current_time, frame_count, drawing_params=None):
        """Xử lý lôgic chính: check spatial memory, nhận kết quả queue, gửi queue, vẽ biển số lên frame."""
        # Lấy tham số vẽ linh hoạt
        f_scale, f_thick, f_offset = (0.7, 2, 10)
        if drawing_params:
            f_scale, f_thick, f_offset = drawing_params
        else:
            h, w = frame.shape[:2]
            f_scale = max(0.45, 0.55 * (w / 1280))
            f_thick = max(1, int(round(2 * (w / 1280))))
            f_offset = max(10, int(round(15 * (w / 1280))))

        # Lọc biển số: Chỉ xử lý OCR nếu tâm biển số nằm trong ô tô/bus/truck
        is_valid_plate = False
        matched_v_id = -1
        for vx1, vy1, vx2, vy2, v_id in valid_vehicles:
            if vx1 <= cx <= vx2 and vy1 <= cy <= vy2:
                is_valid_plate = True
                matched_v_id = v_id
                break
        
        if not is_valid_plate:
            return None

        self.last_seen_plate[track_id] = current_time

        w, h = x2 - x1, y2 - y1
        if w <= 20 or h <= 10: return track_id

        # 1. Kế thừa Spatial Memory
        if track_id not in self.plate_confirmed:
            for old_id, (old_cx, old_cy, old_text, old_frame) in list(self.spatial_memory.items()):
                if frame_count - old_frame < 300 and math.hypot(cx - old_cx, cy - old_cy) < 100:
                    self.plate_confirmed[track_id] = old_text
                    self.plate_raw_cache[track_id] = "INHERITED"
                    break

        self.active_tracks[track_id] = {'bbox': (x1, y1, x2, y2), 'missed_frames': 0}

        display_text = "..."
        if track_id in self.plate_confirmed:
            final_text = self.plate_confirmed[track_id]
            display_text = f"[OK] {final_text}"
            self.spatial_memory[track_id] = (cx, cy, final_text, frame_count)
            if matched_v_id != -1:
                self.vehicle_plates[matched_v_id] = final_text
            
        elif track_id in self.pending_results:
            # Nhận kết quả từ Queue
            res = self.pending_results.pop(track_id)
            final_text = res['final_text']
            self.plate_raw_cache[track_id] = res['clean_text']

            # Lọc Regex
            if ocr_processor.is_valid_vn_plate(final_text):
                if track_id not in self.plate_history: self.plate_history[track_id] = []
                self.plate_history[track_id].append(final_text)

                counter = Counter(self.plate_history[track_id])
                best, count = counter.most_common(1)[0]

                if count >= self.VOTE_THRESHOLD:
                    self.plate_confirmed[track_id] = best
                    display_text = f"[OK] {best}"
                    self.spatial_memory[track_id] = (cx, cy, best, frame_count)
                    if matched_v_id != -1:
                        self.vehicle_plates[matched_v_id] = best
                    if self.alpr_logger:
                        self.alpr_logger.process_plate(best, frame_count, res['img_before'], clean_frame, [x1, y1, x2, y2], v_track_id=matched_v_id)
                else:
                    display_text = f"[?] {best} ({count}/{self.VOTE_THRESHOLD})"
            else:
                display_text = f"[SKIP] {final_text}" if final_text else "..."

            # Cập nhật cửa sổ debug (Tùy chọn hiển thị)
            img_before_r = cv2.resize(res['img_before'], (res['dst_w'], res['dst_h']))
            img_after_r = cv2.resize(res['img_processed'], (res['dst_w'], res['dst_h']))
            cv2.putText(img_after_r, f"RAW: {res['clean_text']}", (5, res['dst_h'] - 35), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0,165,255), 2)
            cv2.putText(img_after_r, f"FIX: {display_text}", (5, res['dst_h'] - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0,255,255), 2)
            self.last_comparison_window = np.vstack((img_before_r, img_after_r))

        else:
            display_text = (self.plate_history.get(track_id) or [self.plate_raw_cache.get(track_id, "...")])[-1]

        # Gửi ảnh mới vào Queue để đọc (Chỉ gửi nếu chưa confirm)
        if track_id not in self.plate_confirmed and frame_count % self.OCR_INTERVAL == 0:
            pad = 2
            x1_p, y1_p = max(0, x1 - pad), max(0, y1 - pad)
            x2_p, y2_p = min(clean_frame.shape[1], x2 + pad), min(clean_frame.shape[0], y2 + pad)
            img_crop = clean_frame[y1_p:y2_p, x1_p:x2_p].copy()
            try:
                self.queue.put_nowait((track_id, img_crop))
            except queue.Full:
                pass

        # Vẽ Box Biển Số & Label có nền
        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), f_thick)
        color = (0, 255, 0) if "[OK]" in display_text else ((0, 0, 255) if "[SKIP]" in display_text else (0, 255, 255))
        
        (tw, th), baseline = cv2.getTextSize(display_text, cv2.FONT_HERSHEY_SIMPLEX, f_scale, f_thick)
        text_y = max(th + 5, y1 - f_offset)
        cv2.rectangle(frame, (x1, text_y - th - 5), (x1 + tw + 5, text_y + baseline + 2), color, -1)
        cv2.putText(frame, display_text, (x1 + 2, text_y), cv2.FONT_HERSHEY_SIMPLEX, f_scale, (255, 255, 255), f_thick)
        
        return track_id

    def show_debug_window(self):
        """Hiển thị cửa sổ OCR debug nếu có cập nhật"""
        if self.last_comparison_window is not None:
            cv2.imshow("Before vs After Processing", self.last_comparison_window)
