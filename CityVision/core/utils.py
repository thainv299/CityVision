import json
from pathlib import Path
from typing import Any, Dict, Optional
from fastapi.responses import JSONResponse
from werkzeug.security import generate_password_hash
from core.config import VALID_ROLES, PROJECT_ROOT, DEFAULT_MODEL_PATH

def json_error(message: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"ok": False, "error": message})

def to_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}

def parse_polygon(value: Any) -> Optional[list[list[int]]]:
    if value in (None, "", []):
        return None
    if isinstance(value, str):
        try:
            data = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("Polygon JSON không hợp lệ.") from exc
    else:
        data = value
    if not isinstance(data, list):
        raise ValueError("Polygon phải là một mảng điểm.")
    normalized: list[list[int]] = []
    for point in data:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ValueError("Mỗi điểm polygon phải có đúng 2 tọa độ.")
        normalized.append([int(point[0]), int(point[1])])
    if normalized and len(normalized) < 3:
        raise ValueError("Polygon cần tối thiểu 3 điểm.")
    return normalized or None

def parse_float(value: Any, default: float) -> float:
    if value in (None, ""):
        return default
    return float(value)

def parse_int(value: Any, default: int) -> int:
    if value in (None, ""):
        return default
    return int(value)

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

def validate_user_payload(payload: Dict[str, Any], creating: bool) -> Dict[str, Any]:
    username = str(payload.get("username", "")).strip()
    full_name = str(payload.get("full_name", "")).strip()
    role = str(payload.get("role", "operator")).strip().lower()
    password = str(payload.get("password", "")).strip()

    if not username:
        raise ValueError("Tên đăng nhập không được để trống.")
    if not full_name:
        raise ValueError("Họ tên không được để trống.")
    if role not in VALID_ROLES:
        raise ValueError("Vai trò không hợp lệ.")
    if creating and len(password) < 6:
        raise ValueError("Mật khẩu cần tối thiểu 6 ký tự.")

    normalized = {
        "username": username,
        "full_name": full_name,
        "role": role,
        "is_active": to_bool(payload.get("is_active"), True),
    }
    if password:
        normalized["password_hash"] = generate_password_hash(password)
    return normalized

def validate_camera_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    name = str(payload.get("name", "")).strip()
    if not name:
        raise ValueError("Tên camera không được để trống.")

    normalized = {
        "name": name,
        "stream_source": str(payload.get("stream_source", "")).strip(),
        "description": str(payload.get("description", "")).strip(),
        "roi_points": parse_polygon(payload.get("roi_points")),
        "no_parking_points": parse_polygon(payload.get("no_parking_points")),
        "enable_congestion": to_bool(payload.get("enable_congestion"), True),
        "enable_illegal_parking": to_bool(payload.get("enable_illegal_parking"), True),
        "enable_license_plate": to_bool(payload.get("enable_license_plate"), True),
        "is_active": to_bool(payload.get("is_active"), True),
    }
    return normalized

def build_test_settings(form_data: Any, camera: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    model_path_text = str(form_data.get("model_path", "")).strip() or str(DEFAULT_MODEL_PATH)
    model_path = resolve_path(model_path_text)
    if not model_path.exists():
        raise ValueError(f"Không tìm thấy model: {model_path}")

    roi_value = form_data.get("roi_points")
    parking_value = form_data.get("no_parking_points")
    roi_points = parse_polygon(roi_value) if roi_value not in (None, "") else None
    no_parking_points = parse_polygon(parking_value) if parking_value not in (None, "") else None

    if camera is not None:
        if roi_points is None:
            roi_points = camera.get("roi_points")
        if no_parking_points is None:
            no_parking_points = camera.get("no_parking_points")

    enable_congestion = to_bool(form_data.get("enable_congestion"), True) if "enable_congestion" in form_data else (camera["enable_congestion"] if camera else True)
    enable_illegal_parking = to_bool(form_data.get("enable_illegal_parking"), True) if "enable_illegal_parking" in form_data else (camera["enable_illegal_parking"] if camera else True)
    enable_license_plate = to_bool(form_data.get("enable_license_plate"), True) if "enable_license_plate" in form_data else (camera["enable_license_plate"] if camera else True)

    return {
        "model_path": str(model_path),
        "confidence_threshold": parse_float(form_data.get("confidence_threshold"), 0.32),
        "enable_congestion": enable_congestion,
        "enable_illegal_parking": enable_illegal_parking,
        "enable_license_plate": enable_license_plate,
        "stop_seconds": parse_float(form_data.get("stop_seconds"), 30.0),
        "parking_move_threshold_px": parse_float(form_data.get("parking_move_threshold_px"), 10.0),
        "process_every_n_frames": parse_int(form_data.get("process_every_n_frames"), 2),
        "roi_points": roi_points,
        "no_parking_points": no_parking_points,
    }
