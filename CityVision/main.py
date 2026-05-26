import cv2
import numpy as np
import os
os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
import json
import time
from ultralytics import YOLO
import tkinter as tk
from tkinter import filedialog, messagebox
import threading
import re          
from paddleocr import PaddleOCR
from collections import deque
from modules.parking.parking_manager import ParkingManager
from modules.ocr.ocr_manager import OCRManager
from modules.traffic.traffic_monitor import TrafficMonitor
from modules.utils.alpr_logger import ALPRLogger
from modules.utils.traffic_alert_manager import TrafficAlertManager
from modules.utils.interactive_telegram_bot import start_bot_thread
from frontend.database.sqlite_db import log_parking_violation

class_names = {
    0: "Person", 1: "Bicycle", 2: "Car", 3: "Motorcycle", 
    4: "License Plate", 5: "Bus", 6: "Truck"    
}

colors = {
    0: (0, 255, 0), 1: (255, 0, 0), 2: (255, 255, 0), 3: (0, 255, 255), 
    4: (0, 0, 255), 5: (0, 165, 255), 6: (255, 0, 255)
}

class App:
    def __init__(self, root):
        self.root = root
        self.root.title("Vehicle Detection CV")
        self.root.geometry("500x650")
        
        self.video_path = None
        self.model_path = None  
        self.model = None
        self.ocr_reader = None
        self.roi_polygon = None

        self.parking_manager = ParkingManager(root, self)
        self.alpr_logger = ALPRLogger()
        self.traffic_alert_manager = TrafficAlertManager()
        start_bot_thread(self.traffic_alert_manager)
        self.ocr_manager = None
        self.CONF_THRESHOLD = 0.32 # Ngưỡng tin cậy của YOLO
        self.is_detecting = False

        # --- KHỞI TẠO GIAO DIỆN ---
        self.lbl_title = tk.Label(root, text="Phát hiện đông đúc / tắc nghẽn", font=("Arial", 14, "bold"))
        self.lbl_title.pack(pady=5)

        self.frame_top = tk.LabelFrame(root, text="1. Nguồn dữ liệu", font=("Arial", 11, "bold"))
        self.frame_top.pack(fill="x", padx=10, pady=5)

        self.btn_select_model = tk.Button(self.frame_top, text="Chọn Model YOLO", command=self.select_model, width=18, font=("Arial", 10))
        self.btn_select_model.grid(row=0, column=0, padx=5, pady=5)
        self.lbl_model_path = tk.Label(self.frame_top, text="Chưa chọn model", wraplength=250, fg="gray", font=("Arial", 10))
        self.lbl_model_path.grid(row=0, column=1, sticky="w", padx=5)

        self.btn_select = tk.Button(self.frame_top, text="Chọn Video", command=self.select_video, width=18, font=("Arial", 10))
        self.btn_select.grid(row=1, column=0, padx=5, pady=5)
        self.lbl_path = tk.Label(self.frame_top, text="Chưa chọn video", wraplength=250, fg="gray", font=("Arial", 10))
        self.lbl_path.grid(row=1, column=1, sticky="w", padx=5)

        self.frame_layout = tk.LabelFrame(root, text="2. Quản lý Vùng Giám Sát (ROI)", font=("Arial", 11, "bold"))
        self.frame_layout.pack(fill="x", padx=10, pady=5)

        self.btn_load_layout = tk.Button(self.frame_layout, text="Load Layout", command=self.load_layout, width=12, state=tk.NORMAL, font=("Arial", 10))
        self.btn_load_layout.grid(row=0, column=0, padx=5, pady=5)

        self.btn_clear_layout = tk.Button(self.frame_layout, text="Hủy Layout", command=self.clear_layout, width=12, state=tk.NORMAL, font=("Arial", 10))
        self.btn_clear_layout.grid(row=0, column=1, padx=5, pady=5)

        self.btn_draw_roi = tk.Button(self.frame_layout, text="Vẽ Vùng Quan Sát", command=self.open_draw_roi, width=16, state=tk.DISABLED, font=("Arial", 10))
        self.btn_draw_roi.grid(row=0, column=2, padx=5, pady=5)

        self.lbl_layout_status = tk.Label(self.frame_layout, text="Layout: Chưa có", fg="red", font=("Arial", 10, "italic"))
        self.lbl_layout_status.grid(row=1, column=0, columnspan=3, pady=2)

        # UI Vùng Cấm Đỗ
        self.parking_manager.init_ui()

        self.frame_action = tk.Frame(root)
        self.frame_action.pack(fill="x", padx=10, pady=10)

        self.frame_buttons = tk.Frame(self.frame_action)
        self.frame_buttons.pack(pady=5)

        self.btn_start = tk.Button(self.frame_buttons, text="Bắt đầu Detect", command=self.start_detection, width=15, height=2, state=tk.DISABLED, font=("Arial", 12, "bold"), bg="#4CAF50", fg="black")
        self.btn_start.pack(side="left", padx=5)

        self.btn_stop = tk.Button(self.frame_buttons, text="Dừng phân tích", command=self.stop_detection, width=15, height=2, state=tk.DISABLED, font=("Arial", 12, "bold"), bg="#f44336", fg="white")
        self.btn_stop.pack(side="left", padx=5)

        self.lbl_status = tk.Label(root, text="Sẵn sàng", fg="black", font=("Arial", 10))
        self.lbl_status.pack(side="bottom", pady=10)

    def select_model(self):
        path = filedialog.askopenfilename(title="Chọn Model YOLO", filetypes=[("YOLO Model", "*.pt *.engine"), ("All Files", "*.*")])
        if path:
            self.model_path = path
            self.lbl_model_path.config(text=f"{os.path.basename(self.model_path)}", fg="blue")
            self.model = None

    def select_video(self):
        path = filedialog.askopenfilename(title="Chọn Video", filetypes=[("Video Files", "*.mp4 *.avi *.mkv *.mov"), ("All Files", "*.*")])
        if path:
            self.video_path = path
            self.lbl_path.config(text=f"{os.path.basename(self.video_path)}", fg="blue")
            self.btn_start.config(state=tk.NORMAL)
            self.btn_draw_roi.config(state=tk.NORMAL)
            self.parking_manager.enable_draw_btn()
            
            if self.roi_polygon is None:
                video_name = os.path.splitext(os.path.basename(self.video_path))[0]
                layout_path = os.path.join("layouts", f"{video_name}.json")
                if os.path.exists(layout_path):
                    try:
                        with open(layout_path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                            self.roi_polygon = np.array(data["points"])
                        self.lbl_layout_status.config(text=f"Layout: Tự động tải {os.path.basename(layout_path)}", fg="green")
                        self.update_status(f"Đã tải: {os.path.basename(layout_path)}", "green")
                    except Exception as e:
                        pass
            
            if self.parking_manager.no_park_polygon is None:
                video_name = os.path.splitext(os.path.basename(self.video_path))[0]
                parking_layout_path = os.path.join("layouts", f"{video_name}_parking_layout.json")
                if os.path.exists(parking_layout_path):
                    try:
                        with open(parking_layout_path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                            self.parking_manager.no_park_polygon = np.array(data["points"])
                        self.parking_manager.lbl_no_park_status.config(text=f"Vùng cấm: Tự động tải {os.path.basename(parking_layout_path)}", fg="green")
                    except Exception as e:
                        pass

    def load_layout(self):
        path = filedialog.askopenfilename(title="Chọn File Layout", filetypes=[("JSON Files", "*.json")])
        if path:
            with open(path, "r", encoding="utf-8") as f:
                self.roi_polygon = np.array(json.load(f).get("points"))
                self.lbl_layout_status.config(text=f"Layout: Đã load {os.path.basename(path)}", fg="green")

    def clear_layout(self):
        self.roi_polygon = None
        self.lbl_layout_status.config(text="Layout: Chưa có", fg="red")

    def open_draw_roi(self):
        if not self.video_path: return
        cap = cv2.VideoCapture(self.video_path)
        ret, first_frame = cap.read()
        cap.release()
        polygon = self.draw_polygon(first_frame, self.roi_polygon, "Draw ROI", (255, 0, 0))
        if polygon is not None:
            self.roi_polygon = polygon
            self.lbl_layout_status.config(text="Layout: Đã vẽ tạm", fg="orange")
            
            # Thêm prompt lưu file cho nhất quán
            if messagebox.askyesno("Lưu Layout", "Bạn có muốn lưu vùng giám sát (ROI) này không?"):
                video_name = os.path.splitext(os.path.basename(self.video_path))[0]
                save_dir = "layouts"
                os.makedirs(save_dir, exist_ok=True)
                save_path = os.path.join(save_dir, f"{video_name}.json")
                with open(save_path, "w", encoding="utf-8") as f:
                    json.dump({"points": self.roi_polygon.tolist()}, f)
                self.lbl_layout_status.config(text=f"Layout: Đã lưu {os.path.basename(save_path)}", fg="green")

    def draw_polygon(self, frame, existing_polygon, window_name, line_color):
        points = []
        if existing_polygon is not None: points = [tuple(p) for p in existing_polygon.tolist()]
        def mouse(event, x, y, flags, param):
            if event == cv2.EVENT_LBUTTONDOWN: points.append((x, y))
            elif event == cv2.EVENT_RBUTTONDOWN: points.clear()
        clone = frame.copy()
        cv2.namedWindow(window_name)
        cv2.setMouseCallback(window_name, mouse)
        while True:
            temp = clone.copy()
            # Thêm text hướng dẫn
            cv2.putText(temp, "Chuot trai: Ve diem | Chuot phai: Xoa het | Ctrl+Z: Undo | Enter/Esc: Luu", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
            
            for p in points: cv2.circle(temp, p, 5, line_color, -1)
            if len(points) > 1: cv2.polylines(temp, [np.array(points)], False, line_color, 2)
            cv2.imshow(window_name, temp)
            key = cv2.waitKey(1) & 0xFF
            if key == 27 or key == 13: break
            if key == 26 or key == ord('z'): # 26 is Ctrl+Z
                if len(points) > 0:
                    points.pop()
        cv2.destroyWindow(window_name)
        return np.array(points) if len(points) >= 3 else None

    def start_detection(self):
        if not self.video_path or self.roi_polygon is None: return
        self.btn_start.config(state=tk.DISABLED)
        self.btn_stop.config(state=tk.NORMAL)
        self.is_detecting = True
        threading.Thread(target=self.detect_video, daemon=True).start()

    def stop_detection(self):
        self.is_detecting = False
        self.update_status("Đang dừng...", "orange")
        self.btn_stop.config(state=tk.DISABLED)

    def update_status(self, text, color="black"):
        self.root.after(0, lambda: self.lbl_status.config(text=text, fg=color))

    def reset_ui(self):
        self.root.after(0, lambda: self.btn_start.config(state=tk.NORMAL))
        self.root.after(0, lambda: self.btn_stop.config(state=tk.DISABLED))
        self.is_detecting = False

    def load_model(self):
        model = YOLO(self.model_path).to("cuda") if not self.model_path.endswith(".engine") else YOLO(self.model_path, task="detect")
        if self.ocr_reader is None:
            self.ocr_reader = PaddleOCR(lang='en')
            self.ocr_manager = OCRManager(self.ocr_reader, alpr_logger=self.alpr_logger)
        dummy = np.zeros((640, 640, 3), dtype=np.uint8)
        for _ in range(5): model.predict(dummy, verbose=False)
        return model

    def detect_video(self):
        try:
            if self.model is None: self.model = self.load_model()
            if self.ocr_manager:
                self.ocr_manager.start_worker()
            self.update_status("Đang nhận diện...", "green")

            cap = cv2.VideoCapture(self.video_path)
            target_classes = ["person", "bicycle", "car", "motorcycle", "license_plate", "bus", "truck"]
            
            traffic_monitor = TrafficMonitor(roi_polygon=self.roi_polygon)
            roi_contour_area = cv2.contourArea(self.roi_polygon)

            prev_time = time.time()
            fps_frame_count, current_fps, frame_count = 0, 0.0, 0
            last_results = None

            video_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0 
            ideal_frame_time = 1.0 / video_fps

            self.parking_manager.setup_detection(video_fps)
            # Set callback to log parking violations to database
            self.parking_manager.violation_callback = log_parking_violation
            # Get camera_id from settings if available (default to 1)
            camera_id = 1  
            self.parking_manager.camera_id = camera_id

            while cap.isOpened() and self.is_detecting:
                ret, frame = cap.read()
                if not ret: break
                
                clean_frame = frame.copy()
                
                current_time = time.time()
                frame_count += 1

                self.parking_manager.update_buffer(clean_frame.copy())

                # Frame Skipping (Tăng tốc xử lý)
                if frame_count % 2 == 0 and last_results is not None:
                    results = last_results
                else:
                    results = self.model.track(frame, persist=True, tracker="bytetrack.yaml", verbose=False)
                    last_results = results
                
                traffic_monitor.reset_counters()
                current_plate_ids = set()
                
                for r in results:
                    valid_vehicles = []
                    for box in r.boxes:
                        tmp_label = self.model.names[int(box.cls[0])]
                        if tmp_label in ["car", "bus", "truck"]:
                            v_track_id = int(box.id[0]) if box.id is not None else -1
                            valid_vehicles.append((*map(int, box.xyxy[0]), v_track_id))

                    for box in r.boxes:
                        cls_id = int(box.cls[0])
                        label = self.model.names[cls_id]
                        conf = float(box.conf[0])

                        if label not in target_classes or conf <= self.CONF_THRESHOLD:
                            continue

                        track_id = int(box.id[0]) if box.id is not None else -1
                        x1, y1, x2, y2 = map(int, box.xyxy[0])
                        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2

                        # Kiểm tra xem vật thể có nằm trong Vùng ROI hay không
                        if cv2.pointPolygonTest(self.roi_polygon, (cx, cy), False) >= 0:
                            # Lọc nhiễu: Bỏ qua Bounding Box lớn bất thường (> 30% diện tích ROI)
                            # Chỉ áp dụng cho person, motorcycle, bicycle, car (bus/truck bản thân đã to)
                            box_area = (x2 - x1) * (y2 - y1)
                            if label in ("person", "motorcycle", "bicycle", "car") and roi_contour_area > 0 and box_area > roi_contour_area * 0.3:
                                continue
                                
                            box_color = colors.get(cls_id, (255, 255, 255))

                            if label == "person":
                                traffic_monitor.log_person(bbox=(x1, y1, x2, y2))
                                cv2.rectangle(frame, (x1, y1), (x2, y2), box_color, 2)
                                cv2.putText(frame, f"ID:{track_id} person", (x1, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, box_color, 2)
                                
                            elif label in ["car", "motorcycle", "bus", "truck"]:
                                traffic_monitor.log_vehicle(track_id, cx, cy, current_time, bbox=(x1, y1, x2, y2))
                                if track_id != -1:
                                    license_plate = None
                                    if self.ocr_manager:
                                        for p_box in r.boxes:
                                            p_lbl = self.model.names[int(p_box.cls[0])]
                                            if p_lbl == "license_plate":
                                                p_tid = int(p_box.id[0]) if p_box.id is not None else -1
                                                px1, py1, px2, py2 = map(int, p_box.xyxy[0])
                                                pcx, pcy = (px1 + px2) // 2, (py1 + py2) // 2
                                                if x1 <= pcx <= x2 and y1 <= pcy <= y2:
                                                    if p_tid in self.ocr_manager.plate_confirmed:
                                                        license_plate = self.ocr_manager.plate_confirmed[p_tid]
                                                    break                                    
                                    state_display_label, state_box_color = self.parking_manager.process_vehicle(
                                        frame, clean_frame, track_id, label, cx, cy, frame_count, bbox=(x1, y1, x2, y2), license_plate=license_plate
                                    )
                                    
                                    if state_display_label:
                                        # Update plate in active_recordings if new plate was confirmed
                                        if track_id in self.parking_manager.active_recordings and license_plate:
                                            self.parking_manager.active_recordings[track_id]['plate'] = license_plate
                                    
                                    display_label = state_display_label if state_display_label else f"ID:{track_id} {label}"
                                    if state_box_color:
                                        box_color = state_box_color
                                else:
                                    display_label = f"{label}"

                                cv2.rectangle(frame, (x1, y1), (x2, y2), box_color, 2)
                                cv2.circle(frame, (cx, cy), 4, (0, 0, 255), -1)
                                cv2.putText(frame, display_label, (x1, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, box_color, 2)

                            elif label == "license_plate" and track_id != -1:
                                if self.ocr_manager:
                                    processed = self.ocr_manager.process_plate(frame, clean_frame, track_id, x1, y1, x2, y2, cx, cy, valid_vehicles, current_time, frame_count)
                                    if processed:
                                        current_plate_ids.add(processed)

                if self.ocr_manager:
                    self.ocr_manager.draw_grace_period_boxes(frame, current_plate_ids)
                    self.ocr_manager.cleanup_memory(current_time, frame_count)

                avg_speed, status_text, status_color, traffic_level = traffic_monitor.calculate_speed_and_status(current_time, frame.shape)
                self.traffic_alert_manager.update_traffic_state(traffic_level, clean_frame)

                cv2.polylines(frame, [self.roi_polygon], True, (255, 0, 0), 2)
                self.parking_manager.draw_polygon_overlay(frame)
                
                traffic_monitor.draw_status(frame, avg_speed, status_text, status_color)

                curr_time = time.time()
                fps_frame_count += 1
                if curr_time - prev_time >= 1.0:
                    current_fps, prev_time, fps_frame_count = fps_frame_count / (curr_time - prev_time), curr_time, 0

                cv2.putText(frame, f"FPS: {int(current_fps)}", (30, 170), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
                
                if self.ocr_manager:
                    self.ocr_manager.show_debug_window()
                if traffic_level > 0 and not self.traffic_alert_manager.is_acknowledged:
                    cv2.putText(frame, "Press 'A' to Acknowledge Alert", (30, 200), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
                    
                cv2.imshow("Vehicle Detection", frame)

                processing_time = time.time() - current_time 
                wait_time_ms = max(1, int((ideal_frame_time - processing_time) * 1000)) 

                key = cv2.waitKey(wait_time_ms) & 0xFF
                if key == 27:
                    break
                elif key in [ord('a'), ord('A')]:
                    self.traffic_alert_manager.acknowledge_alert()

            cap.release()
            cv2.destroyAllWindows()
            if self.ocr_manager:
                self.ocr_manager.stop_worker()
            self.update_status("Đã hoàn thành!", "black")
            self.reset_ui()

        except Exception as e:
            if self.ocr_manager:
                self.ocr_manager.stop_worker()
            self.root.after(0, lambda: messagebox.showerror("Lỗi", str(e)))
            self.reset_ui()

if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()