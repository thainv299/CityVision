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

    def update(self, track_id: int, center: Tuple[float, float], so_frame: int) -> Tuple[int, bool]:
        """
        Trả về (trạng_thái_hiện_tại, trạng_thái_vừa_thay_đổi)
        """
        if track_id not in self.states:
            self.states[track_id] = {
                "history": [center],
                "state": MOVING,
                "waiting_start_frame": -1,
                "grace_count": 0
            }
            return (MOVING, False)

        car_data = self.states[track_id]
        
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
            # Trạng thái kết thúc cho chuỗi xử lý của chiếc xe này
            return (RECORDING_DONE, False)

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
                if car_data["grace_count"] > 10: # Vượt quá 10 frame ân hạn (xe đã thực sự di chuyển)
                    next_state = MOVING
                    state_just_changed = True
            else:
                car_data["grace_count"] = 0 # Đặt lại ân hạn nếu xe đi chậm lại
                
            if next_state == WAITING and frames_waited >= self.stop_frames:
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