import math
from typing import Dict, Any, Tuple, Optional, List


def compute_box_iou(box1: tuple, box2: tuple) -> Tuple[float, float]:
    """
    Tính Intersection over Union (IoU) và Intersection over Min Area (IoM)
    giữa hai bounding boxes dạng (x1, y1, x2, y2).
    Trả về: (iou, iom)
    """
    xA = max(box1[0], box2[0])
    yA = max(box1[1], box2[1])
    xB = min(box1[2], box2[2])
    yB = min(box2[3], box2[3])

    inter_w = max(0, xB - xA)
    inter_h = max(0, yB - yA)
    inter_area = inter_w * inter_h

    if inter_area <= 0:
        return 0.0, 0.0

    area1 = max(1, (box1[2] - box1[0]) * (box1[3] - box1[1]))
    area2 = max(1, (box2[2] - box2[0]) * (box2[3] - box2[1]))

    union_area = float(area1 + area2 - inter_area)
    iou = inter_area / union_area if union_area > 0 else 0.0
    iom = inter_area / float(min(area1, area2))
    return iou, iom


class StationaryVehicleStabilizer:
    """
    Bộ ổn định Track cho xe tĩnh trong khu vực giám sát (Parking ROI).
    Khắc phục triệt để hiện tượng ByteTrack nhảy ID (ID switch) và tạo 2 ID song song cho cùng 1 xe:
    1. Intra-frame NMS: Loại bỏ các box trùng lặp trên cùng 1 xe trong cùng 1 frame.
    2. Spatial IoU Fusion: Tự động phát hiện và khóa ID cố định cho xe đỗ tại chỗ (IoU >= 0.35 hoặc IoM >= 0.50).
    3. Plate-Based Fusion: Tự động sáp nhập các track đọc ra cùng 1 biển số xe.
    """

    def __init__(self, iou_thresh: float = 0.35, iom_thresh: float = 0.50, max_ghost_frames: int = 150):
        self.iou_thresh = iou_thresh
        self.iom_thresh = iom_thresh
        self.max_ghost_frames = max_ghost_frames

        # canonical_id -> thông tin xe tĩnh
        self.stationary_tracks: Dict[int, Dict[str, Any]] = {}
        # raw_id -> canonical_id
        self.raw_to_canonical: Dict[int, int] = {}
        # license_plate -> canonical_id
        self.plate_to_canonical: Dict[str, int] = {}
        self._current_frame: int = -1
        # canonical_id -> raw_id của xe đã claim trong frame hiện tại
        self._frame_claimed: Dict[int, int] = {}

    def filter_duplicate_indices(self, boxes, vehicle_indices: list) -> set:
        """
        Nhận danh sách indices của các box phương tiện trong result.boxes.
        So sánh IoU/IoM giữa các box phương tiện trong cùng frame.
        Trả về set các indices bị suppressed (loại bỏ box confidence thấp hơn).
        """
        if len(vehicle_indices) <= 1:
            return set()

        sorted_indices = sorted(
            vehicle_indices,
            key=lambda idx: float(boxes[idx].conf[0]) if hasattr(boxes[idx], "conf") and len(boxes[idx].conf) > 0 else 0.0,
            reverse=True
        )
        suppressed = set()
        for i, idx_i in enumerate(sorted_indices):
            if idx_i in suppressed:
                continue
            b1 = list(map(int, boxes[idx_i].xyxy[0]))
            for j in range(i + 1, len(sorted_indices)):
                idx_j = sorted_indices[j]
                if idx_j in suppressed:
                    continue
                b2 = list(map(int, boxes[idx_j].xyxy[0]))
                iou, iom = compute_box_iou(b1, b2)
                if iou >= 0.45 or iom >= 0.60:
                    suppressed.add(idx_j)

        return suppressed

    def filter_duplicate_boxes(self, raw_boxes: list) -> list:
        """
        Intra-frame NMS: Nếu cùng 1 frame có 2 box phương tiện đè lên nhau (IoU >= 0.45 hoặc IoM >= 0.60),
        chỉ giữ lại box có diện tích/confidence tốt hơn, tránh việc 2 ID cùng chạy song song trên 1 xe.
        """
        if len(raw_boxes) <= 1:
            return raw_boxes

        filtered = []
        # Sắp xếp theo confidence giảm dần
        sorted_boxes = sorted(
            raw_boxes,
            key=lambda b: float(b.conf[0]) if hasattr(b, "conf") and len(b.conf) > 0 else 0.0,
            reverse=True
        )
        suppressed = set()

        for i, box_i in enumerate(sorted_boxes):
            if i in suppressed:
                continue
            filtered.append(box_i)
            b1 = list(map(int, box_i.xyxy[0]))
            for j in range(i + 1, len(sorted_boxes)):
                if j in suppressed:
                    continue
                b2 = list(map(int, sorted_boxes[j].xyxy[0]))
                iou, iom = compute_box_iou(b1, b2)
                if iou >= 0.45 or iom >= 0.60:
                    suppressed.add(j)

        return filtered

    def get_canonical_id(self, raw_id: int, bbox: tuple, center: tuple, frame_index: int, plate: Optional[str] = None) -> int:
        """
        Xác định ID chuẩn (canonical ID) cho phương tiện một cách ổn định, không nhảy ID:
        1. Reset frame_claimed khi sang frame mới (chống 2 xe khác nhau tranh chấp 1 ID).
        2. Nếu raw_id đã có canonical ID trong self.raw_to_canonical -> LUÔN TRẢ VỀ ID ĐÓ.
        3. Khớp theo Biển số xe nếu đã ghi nhận trước đó.
        4. Khớp không gian Spatial IoU / IoM với các xe tĩnh trong bộ nhớ.
        5. Nếu là xe mới hoàn toàn -> canonical_id = raw_id.
        """
        if raw_id == -1:
            return -1

        if frame_index != self._current_frame:
            self._current_frame = frame_index
            self._frame_claimed.clear()

        # 1. Nếu raw_id này đã được gán canonical ID -> Luôn giữ nguyên ID đó!
        if raw_id in self.raw_to_canonical:
            canonical = self.raw_to_canonical[raw_id]
            if plate and not str(plate).startswith("ID_"):
                self.plate_to_canonical[str(plate).strip().upper()] = canonical
            self._frame_claimed[canonical] = raw_id
            self._update_track(canonical, bbox, center, frame_index, plate)
            return canonical

        # 2. Khớp theo biển số xe đã được ghi nhận trước đó
        if plate and not str(plate).startswith("ID_"):
            clean_plate = str(plate).strip().upper()
            if clean_plate in self.plate_to_canonical:
                canonical = self.plate_to_canonical[clean_plate]
                claimer = self._frame_claimed.get(canonical)
                if claimer is None or claimer == raw_id:
                    self.raw_to_canonical[raw_id] = canonical
                    self._frame_claimed[canonical] = raw_id
                    self._update_track(canonical, bbox, center, frame_index, clean_plate)
                    return canonical

        # 3. Spatial IoU Matching với các xe tĩnh đang theo dõi trong bộ nhớ
        best_match_id = None
        best_score = 0.0

        for cid, info in list(self.stationary_tracks.items()):
            # Không cho phép 2 xe khác nhau trong cùng 1 frame tranh chấp cùng 1 ID
            claimer = self._frame_claimed.get(cid)
            if claimer is not None and claimer != raw_id:
                continue

            if frame_index - info["last_seen"] > self.max_ghost_frames:
                continue

            iou, iom = compute_box_iou(bbox, info["bbox"])
            score = max(iou, iom * 0.85)

            if (iou >= self.iou_thresh or iom >= self.iom_thresh) and score > best_score:
                best_score = score
                best_match_id = cid

        if best_match_id is not None:
            self.raw_to_canonical[raw_id] = best_match_id
            self._frame_claimed[best_match_id] = raw_id
            if plate and not str(plate).startswith("ID_"):
                self.plate_to_canonical[str(plate).strip().upper()] = best_match_id
            self._update_track(best_match_id, bbox, center, frame_index, plate)
            return best_match_id

        # 4. Xe mới hoàn toàn: canonical ID ban đầu chính là raw_id
        canonical = raw_id
        self.raw_to_canonical[raw_id] = canonical
        self._frame_claimed[canonical] = raw_id
        if plate and not str(plate).startswith("ID_"):
            self.plate_to_canonical[str(plate).strip().upper()] = canonical
        self._update_track(canonical, bbox, center, frame_index, plate)
        return canonical

    def _update_track(self, cid: int, bbox: tuple, center: tuple, frame_index: int, plate: Optional[str]):
        clean_plate = str(plate).strip().upper() if plate and not str(plate).startswith("ID_") else None
        if cid not in self.stationary_tracks:
            self.stationary_tracks[cid] = {
                "bbox": bbox,
                "center": center,
                "last_seen": frame_index,
                "plate": clean_plate,
                "history": [center],
                "is_stationary": False
            }
        else:
            t = self.stationary_tracks[cid]
            t["bbox"] = bbox
            t["center"] = center
            t["last_seen"] = frame_index
            if clean_plate:
                t["plate"] = clean_plate
            t["history"].append(center)
            if len(t["history"]) > 15:
                t["history"].pop(0)

            if len(t["history"]) >= 5:
                dx = t["history"][-1][0] - t["history"][0][0]
                dy = t["history"][-1][1] - t["history"][0][1]
                t["is_stationary"] = (math.hypot(dx, dy) < 15.0)

    def cleanup_old_tracks(self, current_frame: int):
        """Dọn dẹp các track xe đã biến mất quá max_ghost_frames"""
        expired = [cid for cid, info in self.stationary_tracks.items() if current_frame - info["last_seen"] > self.max_ghost_frames]
        for cid in expired:
            del self.stationary_tracks[cid]
            to_del_raw = [k for k, v in self.raw_to_canonical.items() if v == cid]
            for k in to_del_raw:
                del self.raw_to_canonical[k]
            to_del_plate = [k for k, v in self.plate_to_canonical.items() if v == cid]
            for k in to_del_plate:
                del self.plate_to_canonical[k]
