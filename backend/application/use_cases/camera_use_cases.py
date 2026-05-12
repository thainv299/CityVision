import json
from typing import Any, Dict, List

from core.errors import AlreadyExistsError, NotFoundError, ValidationError
from domain.entities.camera import Camera
from domain.repositories.camera_repository import CameraRepository


class CameraUseCases:
    def __init__(self, camera_repo: CameraRepository):
        self.camera_repo = camera_repo

    def list_cameras(self) -> List[Camera]:
        return self.camera_repo.list_all()

    def get_camera(self, camera_id: int) -> Camera:
        camera = self.camera_repo.get_by_id(camera_id)
        if not camera:
            raise NotFoundError("Không tìm thấy camera.")
        return camera

    def _parse_polygon(self, raw_value: Any):
        if raw_value in (None, "", []):
            return None, {}
        
        data = raw_value
        if isinstance(raw_value, str):
            try:
                data = json.loads(raw_value)
            except json.JSONDecodeError as exc:
                raise ValidationError("Polygon JSON không hợp lệ.") from exc

        metadata = {}
        points = data
        if isinstance(data, dict):
            metadata = {k: v for k, v in data.items() if k != "points"}
            points = data.get("points", [])

        if not isinstance(points, list):
            raise ValidationError("Polygon phải là một mảng điểm.")

        parsed_points = []
        for point in points:
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                raise ValidationError("Polygon phải có định dạng [[x,y], ...].")
            parsed_points.append([float(point[0]), float(point[1])])

        if len(parsed_points) < 3:
            raise ValidationError("Polygon cần tối thiểu 3 điểm.")

        return parsed_points, metadata

    def _validate_payload(self, payload: Dict[str, Any]) -> Camera:
        def to_bool(val: Any, default: bool = True) -> bool:
            if val is None: return default
            return str(val).strip().lower() in {"1", "true", "yes", "on"}

        name = str(payload.get("name", "")).strip()
        if not name:
            raise ValidationError("Tên camera không được để trống.")

        roi_points, roi_meta = self._parse_polygon(payload.get("roi_points"))
        no_parking_points, no_park_meta = self._parse_polygon(payload.get("no_parking_points"))

        return Camera(
            id=None,
            name=name,
            stream_source=str(payload.get("stream_source", "")).strip(),
            description=str(payload.get("description", "")).strip(),
            roi_points=roi_points,
            roi_meta=roi_meta,
            no_parking_points=no_parking_points,
            no_park_meta=no_park_meta,
            enable_congestion=to_bool(payload.get("enable_congestion"), True),
            enable_illegal_parking=to_bool(payload.get("enable_illegal_parking"), True),
            enable_license_plate=to_bool(payload.get("enable_license_plate"), True),
            is_active=to_bool(payload.get("is_active"), True),
            model_path=str(payload.get("model_path", "")).strip()
        )
        
    def create_camera(self, payload: Dict[str, Any]) -> Camera:
        camera = self._validate_payload(payload)
        for existing in self.camera_repo.list_all():
            if existing.name == camera.name:
                raise AlreadyExistsError("Tên camera đã tồn tại.")
        return self.camera_repo.create(camera)

    def update_camera(self, camera_id: int, payload: Dict[str, Any]) -> Camera:
        updates = self._validate_payload(payload)
        updates.id = camera_id

        existing = self.get_camera(camera_id)
        for other in self.camera_repo.list_all():
            if other.name == updates.name and other.id != camera_id:
                raise AlreadyExistsError("Tên camera đã tồn tại.")

        updated = self.camera_repo.update(updates)
        if not updated:
            raise NotFoundError("Không tìm thấy camera.")
        return updated

    def delete_camera(self, camera_id: int) -> bool:
        if not self.camera_repo.delete(camera_id):
            raise NotFoundError("Không tìm thấy camera.")
        return True
