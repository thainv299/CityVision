from typing import Optional, List, Dict, Any

from core.config import DATABASE_PATH as DB_PATH
from database.sqlite_db import (
    init_db,
    get_dashboard_stats_data as get_dashboard_stats,
    get_detected_license_plates as list_detected_license_plates,
    get_license_plate_by_date as list_license_plates_by_date,
    log_detected_license_plate
)

from domain.entities.user import User
from domain.entities.camera import Camera
from database.sqlite_user_repo import SqliteUserRepository
from database.sqlite_camera_repo import SqliteCameraRepository

user_repo = SqliteUserRepository()
camera_repo = SqliteCameraRepository()

def __user_to_dict(user: Optional[User]) -> Optional[Dict[str, Any]]:
    if user is None:
        return None
    d = user.to_dict()
    d["password_hash"] = user.password_hash # Inject password_hash for routers
    return d

def __camera_to_dict(camera: Optional[Camera]) -> Optional[Dict[str, Any]]:
    if camera is None:
        return None
    return camera.to_dict()

# --- USERS ---
def get_user_record_by_id(user_id: int) -> Optional[Dict[str, Any]]:
    return __user_to_dict(user_repo.get_by_id(user_id))

def get_user_by_id(user_id: int) -> Optional[Dict[str, Any]]:
    return get_user_record_by_id(user_id)

def get_user_record_by_username(username: str) -> Optional[Dict[str, Any]]:
    return __user_to_dict(user_repo.get_by_username(username))

def list_users() -> List[Dict[str, Any]]:
    return [__user_to_dict(u) for u in user_repo.list_all()]

def count_admin_users() -> int:
    return user_repo.count_admin()

def count_active_users() -> int:
    return sum(1 for u in user_repo.list_all() if u.is_active)

def create_user(payload: dict) -> Dict[str, Any]:
    u = User(
        id=None,
        username=payload.get("username"),
        full_name=payload.get("full_name"),
        password_hash=payload.get("password_hash"),
        role=payload.get("role", "operator"),
        is_active=payload.get("is_active", True)
    )
    return __user_to_dict(user_repo.create(u))

def update_user(user_id: int, payload: dict) -> Optional[Dict[str, Any]]:
    u = User(
        id=user_id,
        username=payload.get("username"),
        full_name=payload.get("full_name"),
        password_hash=payload.get("password_hash"),
        role=payload.get("role"),
        is_active=payload.get("is_active")
        # For partial update, values missing in payload are None
    )
    return __user_to_dict(user_repo.update(u))

def delete_user(user_id: int) -> bool:
    return user_repo.delete(user_id)


# --- CAMERAS ---
def get_camera(camera_id: int) -> Optional[Dict[str, Any]]:
    return __camera_to_dict(camera_repo.get_by_id(camera_id))

def list_cameras() -> List[Dict[str, Any]]:
    return [__camera_to_dict(c) for c in camera_repo.list_all()]

def create_camera(payload: dict) -> Dict[str, Any]:
    c = Camera(
        id=None,
        name=payload.get("name"),
        stream_source=payload.get("stream_source", ""),
        description=payload.get("description", ""),
        roi_points=payload.get("roi_points"),
        no_parking_points=payload.get("no_parking_points"),
        enable_congestion=payload.get("enable_congestion", True),
        enable_illegal_parking=payload.get("enable_illegal_parking", True),
        enable_license_plate=payload.get("enable_license_plate", True),
        is_active=payload.get("is_active", True)
    )
    return __camera_to_dict(camera_repo.create(c))

def update_camera(camera_id: int, payload: dict) -> Optional[Dict[str, Any]]:
    c = Camera(
        id=camera_id,
        name=payload.get("name"),
        stream_source=payload.get("stream_source"),
        description=payload.get("description"),
        roi_points=payload.get("roi_points"),
        no_parking_points=payload.get("no_parking_points"),
        enable_congestion=payload.get("enable_congestion"),
        enable_illegal_parking=payload.get("enable_illegal_parking"),
        enable_license_plate=payload.get("enable_license_plate"),
        is_active=payload.get("is_active")
    )
    return __camera_to_dict(camera_repo.update(c))

def delete_camera(camera_id: int) -> bool:
    return camera_repo.delete(camera_id)
