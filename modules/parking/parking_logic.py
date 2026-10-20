from __future__ import annotations
import math
from typing import Dict, Tuple

# CÁC TRẠNG THÁI (STATES)
MOVING = 0          # Đang di chuyển
WAITING = 1         # Đang dừng chờ
VIOLATION = 2       # Vi phạm đỗ xe
RECORDING_DONE = 3  # Đã ghi hình xong

class ViolationLogic:
    def __init__(self, stop_seconds: float, move_thr_px: float, cooldown_seconds: float, fps: float = 21.0):
        self.move_thr_px = float(move_thr_px)
        self.stop_frames = int(stop_seconds * fps)
        self.fps = float(fps)
        self.states: Dict[int, Dict] = {}

    def update(self, track_id: int, center: Tuple[float, float], so_frame: int, stop_seconds: Optional[float] = None) -> Tuple[int, bool]:
        """
        Trả về (trạng_thái_hiện_tại, trạng_thái_vừa_thay_đổi)
        Hỗ trợ stop_seconds tùy chọn theo từng vùng (nếu None sẽ dùng mặc định của hệ thống)
        """
        if track_id not in self.states:
            self.states[track_id] = {
                "history": [center],
                "state": MOVING,
                "waiting_start_frame": -1,
                "grace_count": 0,
                "stop_seconds": stop_seconds
            }
            return (MOVING, False)

        car_data = self.states[track_id]
        if stop_seconds is not None:
            car_data["stop_seconds"] = stop_seconds
        
        # Thêm vào lịch sử và giữ 10 vị trí gần nhất để tính trung bình di chuyển
        car_data["history"].append(center)
        if len(car_data["history"]) > 10:
            car_data["history"].pop(0)
            
        # Tính tốc độ (khoảng cách trung bình dựa trên lịch sử)
        if len(car_data["history"]) >= 2:
            dx = car_data["history"][-1][0] - car_data["history"][0][0]
            dy = car_data["history"][-1][1] - car_data["history"][0][1]
            avg_speed = math.hypot(dx, dy) / len(car_data["history"])
        else:
            avg_speed = 0.0

        current_state = car_data["state"]
        next_state = current_state
        state_just_changed = False

        if current_state == RECORDING_DONE:
            if avg_speed >= self.move_thr_px:
                car_data["grace_count"] += 1
                if car_data["grace_count"] > 10:
                    next_state = MOVING
                    state_just_changed = True
            else:
                car_data["grace_count"] = 0
            # Vẫn duy trì RECORDING_DONE cho đến khi thực sự di chuyển
            pass

        elif current_state == MOVING:
            if avg_speed < self.move_thr_px:
                next_state = WAITING
                car_data["waiting_start_frame"] = so_frame
                car_data["grace_count"] = 0
                state_just_changed = True

        elif current_state == WAITING:
            frames_waited = so_frame - car_data["waiting_start_frame"]
            
            if avg_speed >= self.move_thr_px:
                car_data["grace_count"] += 1
                if car_data["grace_count"] > 10: # Vượt quá 10 frame (xe đã thực sự di chuyển)
                    next_state = MOVING
                    state_just_changed = True
            else:
                car_data["grace_count"] = 0 # Đặt lại nếu xe đi chậm lại
                
            # Xác định ngưỡng frames cần chờ (ưu tiên cấu hình riêng của vùng nếu có)
            effective_stop_seconds = car_data.get("stop_seconds")
            if effective_stop_seconds is not None:
                required_frames = int(float(effective_stop_seconds) * self.fps)
            else:
                required_frames = self.stop_frames

            if next_state == WAITING and frames_waited >= required_frames:
                next_state = VIOLATION
                state_just_changed = True

        elif current_state == VIOLATION:
            # Đã ở trạng thái VI PHẠM, manager sẽ thu thập video 10s rồi tự gọi set_recording_done
            pass

        car_data["state"] = next_state
        return (next_state, state_just_changed)
        
    def set_recording_done(self, track_id: int):
        if track_id in self.states:
            self.states[track_id]["state"] = RECORDING_DONE