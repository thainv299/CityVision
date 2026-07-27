import cv2
import numpy as np
import os
import json
import tkinter as tk
from tkinter import filedialog
from collections import deque
import threading
import datetime
import time
import math
from .parking_logic import ViolationLogic, MOVING, WAITING, VIOLATION, RECORDING_DONE
from modules.utils.telegram_bot import send_telegram_image, send_telegram_video
from modules.utils.common_utils import ensure_dir, now_ts

class ParkingManager:
    def __init__(self, root, app_instance):
        self.root = root
        self.app = app_instance
        self.app_instance = app_instance
        self.no_park_polygon = None
        
        # --- CẤU HÌNH ĐỖ XE TRÁI PHÉP ---
        self.stop_seconds = 30.0
        self.move_thr_px = 10.0
        self.cooldown_seconds = 30.0
        self.camera_id = 0
        self.violation_callback = None
        self.violation_end_callback = None
        self.violation_records = {} # track_id -> violation_id
        self.telegram_enabled = True
        self.save_violation_frames = True
        self.io_worker = None  # AsyncIOWorker (inject từ bên ngoài)
        self.telegram_bot_token = ""
        self.telegram_chat_id = ""
        self.save_to_db = True

        self.logic = None
        self.frame_buffer = None
        self.fps = 30.0
        self.active_recordings = {}
        self.waiting_vehicles = {}
        self.ghost_tracks = {}
        self.last_seen = {}
        self.track_id_map = {}
        self.pending_telegram_warnings = {}
        self._last_notified_plates = {}


    def init_ui(self):
        self.frame_no_park = tk.LabelFrame(self.root, text="3. Quản lý Vùng Cấm Đỗ", font=("Arial", 11, "bold"))
        self.frame_no_park.pack(fill="x", padx=10, pady=5)

        self.btn_load_no_park = tk.Button(self.frame_no_park, text="Load Vùng Cấm", command=self.load_no_park, width=14, state=tk.NORMAL, font=("Arial", 10))
        self.btn_load_no_park.grid(row=0, column=0, padx=5, pady=5)

        self.btn_clear_no_park = tk.Button(self.frame_no_park, text="Hủy Vùng Cấm", command=self.clear_no_park, width=12, state=tk.NORMAL, font=("Arial", 10))
        self.btn_clear_no_park.grid(row=0, column=1, padx=5, pady=5)

        self.btn_draw_no_park = tk.Button(self.frame_no_park, text="Vẽ Vùng Cấm", command=self.open_draw_no_park, width=14, state=tk.DISABLED, font=("Arial", 10))
        self.btn_draw_no_park.grid(row=0, column=2, padx=5, pady=5)

        self.lbl_no_park_status = tk.Label(self.frame_no_park, text="Vùng cấm: Chưa có", fg="red", font=("Arial", 10, "italic"))
        self.lbl_no_park_status.grid(row=1, column=0, columnspan=3, pady=2)

    def load_no_park(self):
        path = filedialog.askopenfilename(title="Chọn File Vùng Cấm", filetypes=[("JSON Files", "*.json")])
        if path:
            with open(path, "r", encoding="utf-8") as f:
                self.no_park_polygon = np.array(json.load(f).get("points"))
                self.lbl_no_park_status.config(text=f"Vùng cấm: Đã load {os.path.basename(path)}", fg="green")

    def clear_no_park(self):
        self.no_park_polygon = None
        self.lbl_no_park_status.config(text="Vùng cấm: Chưa có", fg="red")

    def open_draw_no_park(self):
        if not self.app.video_path: return
        cap = cv2.VideoCapture(self.app.video_path)
        ret, first_frame = cap.read()
        cap.release()
        polygon = self.app.draw_polygon(first_frame, self.no_park_polygon, "Draw No Parking Zone", (0, 0, 255))
        if polygon is not None:
            self.no_park_polygon = polygon
            self.lbl_no_park_status.config(text="Vùng cấm: Đã vẽ tạm", fg="orange")
            
            # Thêm prompt lưu file
            from tkinter import messagebox
            if messagebox.askyesno("Lưu Vùng Cấm", "Bạn có muốn lưu vùng cấm đỗ này không?"):
                video_name = os.path.splitext(os.path.basename(self.app.video_path))[0]
                save_dir = "layouts"
                os.makedirs(save_dir, exist_ok=True)
                save_path = os.path.join(save_dir, f"{video_name}_parking_layout.json")
                with open(save_path, "w", encoding="utf-8") as f:
                    json.dump({"points": self.no_park_polygon.tolist()}, f)
                self.lbl_no_park_status.config(text=f"Vùng cấm: Đã lưu {os.path.basename(save_path)}", fg="green")

    def enable_draw_btn(self):
        self.btn_draw_no_park.config(state=tk.NORMAL)

    def setup_detection(self, fps):
        self.fps = fps
        ensure_dir("logs/violations")
        self.logic = ViolationLogic(self.stop_seconds, self.move_thr_px, self.cooldown_seconds, fps=fps)
        self.frame_buffer = deque(maxlen=int(5 * fps))
        self.active_recordings = {}
        self.waiting_vehicles = {}
        self.ghost_tracks = {}
        self.last_seen = {}
        self.track_id_map = {}
        self.pending_telegram_warnings = {}
        self._last_notified_plates = {}
        # Dict nhận cập nhật biển số async từ OCR: {track_id: plate_str}
        self._pending_plate_updates: dict = {}


    def cancel_pending_warning(self, track_id: int):
        """Hủy bỏ Timer debounce cảnh báo nếu xe di chuyển hoặc bị Re-ID"""
        if hasattr(self, 'pending_telegram_warnings') and track_id in self.pending_telegram_warnings:
            timer_data = self.pending_telegram_warnings.pop(track_id)
            try:
                timer_data['timer'].cancel()
                print(f"[ParkingManager] Đã hủy cảnh báo Telegram bị debounce cho ID:{track_id} do xe đã di chuyển hoặc được Re-ID.")
            except Exception as e:
                pass


    def schedule_telegram_warning(self, track_id: int, img_t0, caption_template: str, initial_plate: str | None = None):
        """Lên lịch gửi cảnh báo Telegram sau 3 giây để debounce và lọc trùng lặp"""
        self.cancel_pending_warning(track_id)

        def fire():
            # 1. Kiểm tra xe còn trong danh sách giám sát (waiting hoặc recording) không
            if track_id not in self.waiting_vehicles and track_id not in self.active_recordings:
                print(f"[ParkingManager] Bỏ qua gửi cảnh báo Telegram cho ID:{track_id} do xe không còn đỗ.")
                return

            # 2. Lấy biển số xe mới nhất được cập nhật từ OCR
            current_plate = None
            if track_id in self.waiting_vehicles:
                current_plate = self.waiting_vehicles[track_id].get('plate')
            elif track_id in self.active_recordings:
                current_plate = self.active_recordings[track_id].get('plate')
            
            if not current_plate:
                current_plate = initial_plate

            # 3. Kiểm tra lọc trùng lặp theo biển số (trong 45 giây gần nhất)
            if current_plate and not current_plate.startswith("ID_"):
                now = time.time()
                # Dọn dẹp entries cũ hơn 5 phút để tránh memory leak
                stale_plates = [p for p, t in self._last_notified_plates.items() if now - t > 300]
                for p in stale_plates:
                    del self._last_notified_plates[p]

                last_time = self._last_notified_plates.get(current_plate, 0)
                if now - last_time < 45.0:
                    print(f"[ParkingManager] Bỏ qua gửi cảnh báo trùng lặp Telegram cho biển số {current_plate} (debounce).")
                    return
                self._last_notified_plates[current_plate] = now

            # 4. Tạo lại caption với biển số xe thực tế nếu có
            if current_plate and not current_plate.startswith("ID_"):
                final_caption = f"⚠️ CẢNH BÁO: Xe biển số {current_plate} bắt đầu đỗ tại vùng cấm. Đang đếm giờ..."
            else:
                final_caption = f"⚠️ CẢNH BÁO: Phát hiện xe bắt đầu đỗ tại vùng cấm. Đang đếm giờ..."

            # 5. Gửi cảnh báo
            threading.Thread(target=self._send_warning_thread, args=(img_t0, final_caption), daemon=True).start()

        t = threading.Timer(3.0, fire)
        t.daemon = True
        self.pending_telegram_warnings[track_id] = {
            'timer': t,
            'caption': caption_template
        }
        t.start()
        print(f"[ParkingManager] Đã lên lịch gửi cảnh báo Telegram cho ID:{track_id} sau 3 giây (debounce).")


    def update_plate(self, track_id: int, plate: str):
        """
        Cập nhật biển số xe cho một track_id từ OCR bất đồng bộ.
        Nếu xe đang trong active_recordings, cập nhật ngay; nếu chưa, lưu vào pending
        để áp dụng trước khi lưu bằng chứng.
        """
        if not plate:
            return
        mapped_id = self.track_id_map.get(track_id, track_id)
        if mapped_id in self.active_recordings:
            self.active_recordings[mapped_id]['plate'] = plate
        if mapped_id in self.waiting_vehicles:
            self.waiting_vehicles[mapped_id]['plate'] = plate
        else:
            # Lưu tạm, update_buffer sẽ áp dụng khi recording bắt đầu
            self._pending_plate_updates[mapped_id] = plate

    def update_buffer(self, frame_copy):
        if self.frame_buffer is not None:
            self.frame_buffer.append(frame_copy)
            
            to_delete = []
            for track_id, record_data in self.active_recordings.items():
                # Áp dụng cập nhật biển số đang chờ (OCR async)
                if track_id in self._pending_plate_updates:
                    record_data['plate'] = self._pending_plate_updates.pop(track_id)

                record_data['frames'].append(frame_copy)
                record_data['frames_needed'] -= 1
                if record_data['frames_needed'] <= 0:
                    if self.save_to_db:
                        threading.Thread(target=self._save_evidence_and_notify_thread, args=(track_id, record_data), daemon=True).start()
                    self.logic.set_recording_done(track_id)
                    to_delete.append(track_id)
                    
            for tid in to_delete:
                del self.active_recordings[tid]

    def _send_warning_thread(self, img_t0, caption):
        # Nếu có io_worker, dùng async mode (đẩy vào queue)
        if self.io_worker is not None:
            self.io_worker.enqueue_telegram_image_from_frame(
                img_t0, caption, self.telegram_bot_token, self.telegram_chat_id
            )
            return

        # Fallback: gọi đồng bộ (legacy, cho desktop GUI)
        temp_dir = os.path.join("logs", "violations", "_temp")
        os.makedirs(temp_dir, exist_ok=True)
        img_path = os.path.join(temp_dir, f"temp_warning_{now_ts()}.jpg")
        cv2.imwrite(img_path, img_t0)
        send_telegram_image(img_path, caption, self.telegram_bot_token, self.telegram_chat_id)
        try: os.remove(img_path)
        except: pass

    def _save_evidence_and_notify_thread(self, track_id, data):
        now = datetime.datetime.now()
        evt_id = f"EVT_{now.strftime('%Y%m%d_%H%M%S')}_{track_id}"
        # Dùng biển số thực nếu đọc được, fallback về ID_{track_id} nếu không có
        raw_plate = data.get('plate')
        plate_folder = raw_plate if raw_plate else f"ID_{track_id}"
        # Cây thư mục: logs/violations/năm/tháng/ngày/biển_số/evt_id
        save_dir = os.path.join(
            "logs", "violations",
            now.strftime('%Y'),
            now.strftime('%m'),
            now.strftime('%d'),
            plate_folder,
            evt_id
        )
        os.makedirs(save_dir, exist_ok=True)
        
        combined_path = os.path.join(save_dir, "combined_alert.jpg")
        
        if self.save_to_db:
            img_t0_path = os.path.join(save_dir, "img_T0.jpg")
            img_t1_path = os.path.join(save_dir, "img_T1.jpg")
            video_path = os.path.join(save_dir, "video_record.mp4")
            json_path = os.path.join(save_dir, "evidence.json")
            
            cv2.imwrite(img_t0_path, data['img_t0'])
            cv2.imwrite(img_t1_path, data['img_t1'])
        
        # Ghép ảnh thông báo nguyên khối (T0 + T1) với nền text
        h1, w1 = data['img_t0'].shape[:2]
        h2, w2 = data['img_t1'].shape[:2]
        target_w = max(w1, w2)
        img1 = cv2.resize(data['img_t0'], (target_w, int(h1 * target_w / w1)))
        img2 = cv2.resize(data['img_t1'], (target_w, int(h2 * target_w / w2)))
        
        # Tham số vẽ linh hoạt
        comb_h, comb_w = (img1.shape[0] + img2.shape[0]), target_w
        f_scale = max(0.6, 1.0 * (comb_h / 1440))
        f_thick = max(1, int(round(2 * (comb_h / 1440))))
        
        def put_text_with_bg(img, text, pos, scale, color, thick):
            (tw, th), bl = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, thick)
            x, y = pos
            cv2.rectangle(img, (x - 5, y - th - 5), (x + tw + 5, y + bl + 5), (0, 0, 0), -1)
            cv2.putText(img, text, pos, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thick)

        put_text_with_bg(img1, "T0: Bat dau do", (int(20*(target_w/1280)), int(60*(h1/720))), f_scale, (0, 0, 255), f_thick)
        put_text_with_bg(img2, "T1: Vi pham", (int(20*(target_w/1280)), int(60*(h2/720))), f_scale, (0, 0, 255), f_thick)
        combined = np.vstack((img1, img2))
        put_text_with_bg(combined, f"PLATE: {plate_folder}", (int(20*(target_w/1280)), int(100*(comb_h/1440))), f_scale * 1.2, (0, 255, 0), f_thick + 1)
        cv2.imwrite(combined_path, combined)

        # Lưu video bằng chứng
        if self.save_to_db and data['frames']:
            fh, fw = data['frames'][0].shape[:2]
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            out = cv2.VideoWriter(video_path, fourcc, self.fps, (fw, fh))
            for f in data['frames']:
                out.write(f)
            out.release()
            
            # Convert to H.264 for web browser compatibility
            try:
                import subprocess, shutil
                temp_vid = video_path.replace(".mp4", "_temp.mp4")
                shutil.move(video_path, temp_vid)
                
                ffmpeg_cmd = shutil.which("ffmpeg")
                if not ffmpeg_cmd:
                    local_appdata = os.environ.get("LOCALAPPDATA", "")
                    winget_packages = os.path.join(local_appdata, "Microsoft", "WinGet", "Packages")
                    if os.path.exists(winget_packages):
                        for root, dirs, files in os.walk(winget_packages):
                            if "ffmpeg.exe" in files:
                                ffmpeg_cmd = os.path.join(root, "ffmpeg.exe")
                                break
                if not ffmpeg_cmd:
                    ffmpeg_cmd = "ffmpeg"
                
                subprocess.run([
                    ffmpeg_cmd, "-i", temp_vid, "-c:v", "libx264", "-preset", "fast", "-y", video_path
                ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                
                if os.path.exists(video_path) and os.path.getsize(video_path) > 0:
                    if os.path.exists(temp_vid): os.remove(temp_vid)
                else:
                    shutil.move(temp_vid, video_path)
            except Exception as e:
                print(f"[ParkingManager] Lỗi convert video sang H.264: {e}")
                temp_vid = video_path.replace(".mp4", "_temp.mp4")
                if os.path.exists(temp_vid) and not os.path.exists(video_path):
                    shutil.move(temp_vid, video_path)
            
        # Lưu file Metadata JSON
        meta = {
            "track_id": track_id,
            "plate": plate_folder,
            "label": data.get('label', ''),
            "start_time": data.get('start_time', datetime.datetime.now()).strftime('%Y-%m-%d %H:%M:%S'),
            "violation_time": datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "bbox": data.get('bbox')
        }
        if self.save_to_db:
            with open(json_path, 'w', encoding='utf-8') as jf:
                json.dump(meta, jf, indent=4)
            
        # Gọi callback để lưu vi phạm vào Database với đầy đủ thông tin
        if self.violation_callback:
            try:
                # Đường dẫn ảnh tương đối để web có thể hiển thị
                db_image_path = os.path.join(save_dir, "combined_alert.jpg").replace("\\", "/")
                violation_id = self.violation_callback(
                    camera_id=self.camera_id,
                    license_plate=plate_folder,
                    violation_time=meta['start_time'],
                    duration=int(self.stop_seconds),
                    frame_path=db_image_path
                )
                if violation_id:
                    self.violation_records[track_id] = violation_id
            except Exception as e:
                print(f"[ParkingManager] Lỗi gọi callback DB: {e}")

        print(f"[ParkingManager] Đã xử lý bằng chứng cho ID:{track_id} tại {save_dir}")
            
        if self.telegram_enabled and self.save_to_db:
            raw_plate = data.get('plate')
            if raw_plate and not raw_plate.startswith("ID_"):
                caption_img = f"🚨 VI PHẠM CHỐT: Xe biển số {raw_plate} đỗ sai quy định."
                caption_vid = f"Bằng chứng Video 15s cho xe biển số {raw_plate}"
            else:
                caption_img = f"🚨 VI PHẠM CHỐT: Phát hiện xe đỗ sai quy định (Không rõ biển số)."
                caption_vid = f"Bằng chứng Video 15s phát hiện xe đỗ sai quy định"
            if self.io_worker is not None:
                # Async mode: đẩy vào queue
                self.io_worker.enqueue_telegram_image(
                    combined_path, caption_img, self.telegram_bot_token, self.telegram_chat_id
                )
                self.io_worker.enqueue_telegram_video(
                    video_path, caption_vid, self.telegram_bot_token, self.telegram_chat_id
                )
            else:
                # Fallback: gọi đồng bộ
                send_telegram_image(combined_path, caption_img, self.telegram_bot_token, self.telegram_chat_id)
                send_telegram_video(video_path, caption_vid, self.telegram_bot_token, self.telegram_chat_id)

    def process_vehicle(self, frame, clean_frame, track_id, label, cx, cy, frame_count, bbox=None, license_plate=None, drawing_params=None):
        """Kiểm tra và cập nhật trạng thái đỗ xe, trả về display_label và box_color mới (nếu có)"""
        if self.logic is None:
            return None, None
            
        # Lấy tham số vẽ linh hoạt (nếu có)
        f_scale, f_thick, f_offset = (0.7, 2, 10)
        if drawing_params:
            f_scale, f_thick, f_offset = drawing_params
            
        # Bỏ qua xe máy, xe đạp và người đi bộ (không xét lỗi đỗ trái phép)
        if label in ["motorcycle", "bicycle", "person"]:
            return None, None

        current_time = time.time()

        # Resolution mapping cho Re-ID
        mapped_id = self.track_id_map.get(track_id, track_id)

        # 1. Dọn dẹp Ghost Tracks hết hạn (> 10s)
        expired_ghosts = [gid for gid, ginfo in self.ghost_tracks.items() if current_time - ginfo['lost_time'] > 10.0]
        for gid in expired_ghosts:
            if gid in self.violation_records:
                if self.violation_end_callback:
                    self.violation_end_callback(self.violation_records[gid])
                del self.violation_records[gid]
            self.cancel_pending_warning(gid)
            del self.ghost_tracks[gid]
            # Dọn dẹp bản đồ ánh xạ của ID cũ này
            keys_to_del = [k for k, v in self.track_id_map.items() if v == gid]
            for k in keys_to_del:
                del self.track_id_map[k]

        # 2. Phát hiện xe bị mất dấu (> 1s) và đẩy vào Ghost Tracks
        lost_ids = [lid for lid, linfo in self.last_seen.items() if current_time - linfo['last_time'] > 1.0]
        for lid in lost_ids:
            self.cancel_pending_warning(lid)
            self.ghost_tracks[lid] = {
                'cx': self.last_seen[lid]['cx'],
                'cy': self.last_seen[lid]['cy'],
                'lost_time': current_time
            }
            if lid in self.logic.states:
                self.ghost_tracks[lid]['logic_state'] = self.logic.states.pop(lid)
            if lid in self.waiting_vehicles:
                self.ghost_tracks[lid]['waiting_data'] = self.waiting_vehicles.pop(lid)
            del self.last_seen[lid]

        # 3. Thuật toán Spatial Re-ID (Sáp nhập Track vỡ nếu xuất hiện ID mới tại cùng vị trí)
        if track_id not in self.last_seen and track_id not in self.track_id_map:
            best_match = None
            min_dist = float('inf')
            max_dist_px = max(60.0, self.move_thr_px * 2) # Khoảng cách tối đa cho phép nối ghép
            
            for gid, ginfo in self.ghost_tracks.items():
                dist = math.hypot(cx - ginfo['cx'], cy - ginfo['cy'])
                if dist < max_dist_px and dist < min_dist:
                    min_dist = dist
                    best_match = gid
            
            if best_match is not None:
                # Nối ghép thành công! Khôi phục trí nhớ cho xe bằng cách ánh xạ ID mới về ID cũ
                self.track_id_map[track_id] = best_match
                mapped_id = best_match
                self.cancel_pending_warning(track_id)
                
                ginfo = self.ghost_tracks.pop(best_match)
                if 'logic_state' in ginfo:
                    self.logic.states[best_match] = ginfo['logic_state']
                if 'waiting_data' in ginfo:
                    self.waiting_vehicles[best_match] = ginfo['waiting_data']

        # Cập nhật vị trí và dấu thời gian hiện tại dưới ID thực (mapped_id)
        self.last_seen[mapped_id] = {'cx': cx, 'cy': cy, 'last_time': current_time}

        in_no_park = False
        if self.no_park_polygon is not None:
            in_no_park = cv2.pointPolygonTest(self.no_park_polygon, (cx, cy), False) >= 0

        if in_no_park:
            state, just_changed = self.logic.update(mapped_id, (cx, cy), frame_count)
            
            if state == WAITING:
                box_color = (0, 165, 255) # Orange
                state_str = "WAITING"
                if just_changed:
                    img_t0 = clean_frame.copy()
                    self.draw_polygon_overlay(img_t0, f_thick)
                    if bbox is not None:
                        x1, y1, x2, y2 = bbox
                        cv2.rectangle(img_t0, (x1, y1), (x2, y2), box_color, f_thick + 1)
                        txt = f"{label.upper()} {mapped_id} - BAT DAU DO"
                        (tw, th), bl = cv2.getTextSize(txt, cv2.FONT_HERSHEY_SIMPLEX, f_scale, f_thick)
                        ty = max(th + 5, y1 - 5)
                        cv2.rectangle(img_t0, (x1, ty - th - 5), (x1 + tw + 5, ty + bl + 2), box_color, -1)
                        cv2.putText(img_t0, txt, (x1 + 2, ty), cv2.FONT_HERSHEY_SIMPLEX, f_scale, (255, 255, 255), f_thick)
                        
                    self.waiting_vehicles[mapped_id] = {
                        'img_t0': img_t0,
                        'start_time': datetime.datetime.now(),
                        'plate': license_plate
                    }
                    if self.telegram_enabled and self.save_to_db:
                        if license_plate and not license_plate.startswith("ID_"):
                            caption = f"⚠️ CẢNH BÁO: Xe biển số {license_plate} bắt đầu đỗ tại vùng cấm. Đang đếm giờ..."
                        else:
                            caption = f"⚠️ CẢNH BÁO: Phát hiện xe bắt đầu đỗ tại vùng cấm. Đang đếm giờ..."
                        self.schedule_telegram_warning(mapped_id, img_t0, caption, license_plate)
                        
            elif state == VIOLATION:
                box_color = (0, 0, 255) # Red
                state_str = "VIOLATION"
                if just_changed:
                    waiting_data = self.waiting_vehicles.get(mapped_id, {})
                    img_t0 = waiting_data.get('img_t0', clean_frame.copy())
                    start_time = waiting_data.get('start_time', datetime.datetime.now())
                    
                    img_t1 = clean_frame.copy()
                    self.draw_polygon_overlay(img_t1, f_thick)
                    if bbox is not None:
                        x1, y1, x2, y2 = bbox
                        cv2.rectangle(img_t1, (x1, y1), (x2, y2), box_color, f_thick + 2)
                        txt = f"{label.upper()} {mapped_id} - VI PHAM!"
                        (tw, th), bl = cv2.getTextSize(txt, cv2.FONT_HERSHEY_SIMPLEX, f_scale + 0.1, f_thick + 1)
                        ty = max(th + 5, y1 - 5)
                        cv2.rectangle(img_t1, (x1, ty - th - 5), (x1 + tw + 5, ty + bl + 2), box_color, -1)
                        cv2.putText(img_t1, txt, (x1 + 2, ty), cv2.FONT_HERSHEY_SIMPLEX, f_scale + 0.1, (255, 255, 255), f_thick + 1)
                        
                    self.active_recordings[mapped_id] = {
                        'frames': list(self.frame_buffer),
                        'frames_needed': int(10 * self.fps),
                        'img_t0': img_t0,
                        'img_t1': img_t1,
                        'plate': license_plate,  # Lưu biển số thực tế hoặc None - sẽ cập nhật khi OCR xác nhận
                        'start_time': start_time,
                        'label': label,
                        'track_id': mapped_id,  # Lưu track_id gốc làm dự phòng (fallback)
                        'bbox': bbox
                    }
                    
                    h, w = frame.shape[:2]
                    banner_h = int(80 * (h / 720))
                    cv2.rectangle(frame, (0, 0), (w, banner_h), (0, 0, 0), -1)
                    cv2.putText(frame, "VI PHAM: DO XE SAI QUY DINH!", (int(20 * (w/1280)), int(55 * (h/720))), cv2.FONT_HERSHEY_SIMPLEX, 1.1 * (w/1280), (0, 0, 255), f_thick + 1)
                    
            elif state == 3: # RECORDING_DONE
                box_color = (0, 0, 255)
                state_str = "RECORDED"
            else:
                box_color = None
                state_str = "MOVING"
                if just_changed:
                    if mapped_id in self.violation_records:
                        if self.violation_end_callback:
                            self.violation_end_callback(self.violation_records[mapped_id])
                        del self.violation_records[mapped_id]
                    self.cancel_pending_warning(mapped_id)
                    self.waiting_vehicles.pop(mapped_id, None)
                    self.active_recordings.pop(mapped_id, None)
                
            display_label = f"ID:{mapped_id} {label} {state_str}"
            return display_label, box_color
        else:
            # Xe nằm ngoài vùng cấm đỗ -> Reset trạng thái giám sát đỗ xe của xe này nếu có
            if self.logic and mapped_id in self.logic.states:
                if mapped_id in self.violation_records:
                    if self.violation_end_callback:
                        self.violation_end_callback(self.violation_records[mapped_id])
                    del self.violation_records[mapped_id]
                self.cancel_pending_warning(mapped_id)
                self.logic.states.pop(mapped_id, None)
                self.waiting_vehicles.pop(mapped_id, None)
                self.active_recordings.pop(mapped_id, None)

            if mapped_id != track_id:
                return f"ID:{mapped_id} {label}", None
        return None, None


    def draw_polygon_overlay(self, frame, f_thick=2):
        """Vẽ vùng cấm đỗ màu đỏ lên frame"""
        if self.no_park_polygon is not None:
            overlay = frame.copy()
            cv2.fillPoly(overlay, [self.no_park_polygon], (0, 0, 180))
            cv2.addWeighted(overlay, 0.15, frame, 0.85, 0, frame)
            cv2.polylines(frame, [self.no_park_polygon], True, (0, 0, 255), f_thick)