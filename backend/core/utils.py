import textwrap
from pathlib import Path
from typing import Any, Dict, Optional

import cv2
import numpy as np

from core.config import PROJECT_ROOT


def resolve_path(path_text: str) -> Path:
    candidate = Path(path_text).expanduser()
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    return candidate.resolve()


def normalize_capture_source(source: str) -> Any:
    stripped = (source or "").strip()
    if not stripped:
        return None
    if stripped.isdigit():
        return int(stripped)
    return stripped


def build_placeholder_frame(title: str, detail: str = "") -> bytes:
    canvas = np.zeros((360, 640, 3), dtype=np.uint8)
    canvas[:] = (21, 31, 39)
    cv2.rectangle(canvas, (16, 16), (624, 344), (19, 98, 112), 2)
    cv2.putText(
        canvas,
        "Camera Preview",
        (28, 58),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.0,
        (233, 249, 247),
        2,
    )
    cv2.putText(
        canvas,
        title,
        (28, 120),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.8,
        (255, 208, 102),
        2,
    )
    for index, line in enumerate(textwrap.wrap(detail, width=42)[:4]):
        cv2.putText(
            canvas,
            line,
            (28, 170 + (index * 34)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (214, 224, 233),
            2,
        )

    success, encoded = cv2.imencode(".jpg", canvas)
    if not success:
        raise RuntimeError("Không thể tạo ảnh preview.")
    return encoded.tobytes()


def encode_jpeg(frame: np.ndarray) -> bytes:
    success, encoded = cv2.imencode(".jpg", frame)
    if not success:
        raise RuntimeError("Không thể mã hóa ảnh preview.")
    return encoded.tobytes()


def prepare_snapshot_frame(frame: np.ndarray, camera: Dict[str, Any]) -> np.ndarray:
    display = frame.copy()

    if camera.get("roi_points"):
        roi_polygon = np.array(camera["roi_points"], dtype=np.int32)
        cv2.polylines(display, [roi_polygon], True, (255, 0, 0), 2)
    no_park_meta = camera.get("no_park_meta") or {}
    zones = no_park_meta.get("zones")
    if zones and isinstance(zones, list):
        overlay = display.copy()
        for z in zones:
            pts = z.get("points")
            if pts and len(pts) >= 3:
                poly = np.array(pts, dtype=np.int32)
                cv2.fillPoly(overlay, [poly], (0, 0, 160))
        cv2.addWeighted(overlay, 0.15, display, 0.85, 0, display)
        for z in zones:
            pts = z.get("points")
            if pts and len(pts) >= 3:
                poly = np.array(pts, dtype=np.int32)
                cv2.polylines(display, [poly], True, (0, 0, 255), 2)
                z_name = z.get("name") or "Vung cam"
                z_time = z.get("timeout")
                t_str = f" ({int(z_time)}s)" if z_time is not None else ""
                cv2.putText(display, f"{z_name}{t_str}", (int(poly[0][0]), max(15, int(poly[0][1] - 5))),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1)
    elif camera.get("no_parking_points"):
        polygon = np.array(camera["no_parking_points"], dtype=np.int32)
        overlay = display.copy()
        cv2.fillPoly(overlay, [polygon], (0, 0, 160))
        cv2.addWeighted(overlay, 0.15, display, 0.85, 0, display)
        cv2.polylines(display, [polygon], True, (0, 0, 255), 2)

    cv2.rectangle(display, (0, 0), (display.shape[1], 68), (11, 20, 26), -1)
    cv2.putText(
        display,
        camera["name"],
        (18, 28),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.8,
        (255, 255, 255),
        2,
    )
    feature_text = (
        f"Tac nghen: {'ON' if camera['enable_congestion'] else 'OFF'} | "
        f"Do xe sai quy dinh: {'ON' if camera['enable_illegal_parking'] else 'OFF'} | "
        f"Bien so: {'ON' if camera['enable_license_plate'] else 'OFF'}"
    )
    cv2.putText(
        display,
        feature_text,
        (18, 56),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        (171, 208, 199),
        2,
    )
    return display
