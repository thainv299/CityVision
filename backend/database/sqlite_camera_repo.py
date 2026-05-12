import json
import sqlite3
from typing import List, Optional

from domain.entities.camera import Camera
from domain.repositories.camera_repository import CameraRepository
from database.sqlite_db import connect


class SqliteCameraRepository(CameraRepository):

    def _load_polygon(self, raw_value: Optional[str]):
        if not raw_value:
            return None, {}
        try:
            data = json.loads(raw_value)
        except:
            return None, {}
            
        points = data
        metadata = {}
        if isinstance(data, dict):
            points = data.get("points", [])
            metadata = {k: v for k, v in data.items() if k != "points"}

        if not isinstance(points, list):
            return None, {}
            
        normalized: List[List[float]] = []
        for point in points:
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                continue
            normalized.append([float(point[0]), float(point[1])])
        return (normalized or None), metadata

    def _dump_polygon(self, points: Optional[List[List[float]]], metadata: Optional[dict]) -> Optional[str]:
        if not points:
            return None
        payload = {"points": points}
        if metadata:
            payload.update(metadata)
        return json.dumps(payload, ensure_ascii=False)

    def _row_to_camera(self, row: sqlite3.Row) -> Camera:
        roi_pts, roi_m = self._load_polygon(row["toa_do_vung_chon"])
        no_park_pts, no_park_m = self._load_polygon(row["toa_do_cam_do"])
        
        return Camera(
            id=row["id"],
            name=row["ten_camera"],
            stream_source=row["nguon_phat"] or "",
            description=row["mo_ta"] or "",
            roi_points=roi_pts,
            roi_meta=roi_m,
            no_parking_points=no_park_pts,
            no_park_meta=no_park_m,
            enable_congestion=bool(row["bat_phat_hien_un_tac"]),
            enable_illegal_parking=bool(row["bat_phat_hien_do_sai"]),
            enable_license_plate=bool(row["bat_phat_hien_bien_so"]),
            is_active=bool(row["trang_thai_hoat_dong"]),
            model_path=row["mo_hinh_yolo"] or "",
            created_at=row["ngay_tao"],
            updated_at=row["ngay_cap_nhat"],
        )

    def get_by_id(self, camera_id: int) -> Optional[Camera]:
        with connect() as connection:
            row = connection.execute(
                "SELECT * FROM camera WHERE id = ?", (camera_id,)
            ).fetchone()
        return self._row_to_camera(row) if row else None

    def list_all(self) -> List[Camera]:
        with connect() as connection:
            rows = connection.execute(
                "SELECT * FROM camera ORDER BY ngay_cap_nhat DESC, id DESC"
            ).fetchall()
        return [self._row_to_camera(row) for row in rows]

    def get_active_count(self) -> int:
        with connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS total FROM camera WHERE trang_thai_hoat_dong = 1"
            ).fetchone()
        return int(row["total"])

    def get_feature_counts(self) -> dict:
        with connect() as connection:
            congestion_enabled = int(
                connection.execute(
                    "SELECT COUNT(*) AS total FROM camera WHERE trang_thai_hoat_dong = 1 AND bat_phat_hien_un_tac = 1"
                ).fetchone()["total"]
            )
            illegal_parking_enabled = int(
                connection.execute(
                    "SELECT COUNT(*) AS total FROM camera WHERE trang_thai_hoat_dong = 1 AND bat_phat_hien_do_sai = 1"
                ).fetchone()["total"]
            )
            license_plate_enabled = int(
                connection.execute(
                    "SELECT COUNT(*) AS total FROM camera WHERE trang_thai_hoat_dong = 1 AND bat_phat_hien_bien_so = 1"
                ).fetchone()["total"]
            )
        return {
            "congestion": congestion_enabled,
            "illegal_parking": illegal_parking_enabled,
            "license_plate": license_plate_enabled
        }

    def get_recent(self, limit: int = 6) -> List[Camera]:
        with connect() as connection:
            rows = connection.execute(
                "SELECT * FROM camera ORDER BY ngay_cap_nhat DESC, id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._row_to_camera(row) for row in rows]

    def create(self, camera: Camera) -> Camera:
        with connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO camera (
                    ten_camera, nguon_phat, mo_ta, toa_do_vung_chon, toa_do_cam_do,
                    bat_phat_hien_un_tac, bat_phat_hien_do_sai, bat_phat_hien_bien_so, trang_thai_hoat_dong, mo_hinh_yolo
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    camera.name,
                    camera.stream_source,
                    camera.description,
                    self._dump_polygon(camera.roi_points, camera.roi_meta),
                    self._dump_polygon(camera.no_parking_points, camera.no_park_meta),
                    int(camera.enable_congestion),
                    int(camera.enable_illegal_parking),
                    int(camera.enable_license_plate),
                    int(camera.is_active),
                    camera.model_path,
                ),
            )
            connection.commit()
            camera_id = int(cursor.lastrowid)

        created = self.get_by_id(camera_id)
        if not created:
            raise RuntimeError("Lỗi DB khi tạo Camera")
        return created

    def update(self, camera: Camera) -> Optional[Camera]:
        assignments = []
        values = []

        if camera.name is not None:
            assignments.append("ten_camera = ?")
            values.append(camera.name)
        if camera.stream_source is not None:
            assignments.append("nguon_phat = ?")
            values.append(camera.stream_source)
        if camera.description is not None:
            assignments.append("mo_ta = ?")
            values.append(camera.description)
        if camera.roi_points is not None:
            assignments.append("toa_do_vung_chon = ?")
            values.append(self._dump_polygon(camera.roi_points, camera.roi_meta))
        if camera.no_parking_points is not None:
            assignments.append("toa_do_cam_do = ?")
            values.append(self._dump_polygon(camera.no_parking_points, camera.no_park_meta))
        if camera.enable_congestion is not None:
            assignments.append("bat_phat_hien_un_tac = ?")
            values.append(int(camera.enable_congestion))
        if camera.enable_illegal_parking is not None:
            assignments.append("bat_phat_hien_do_sai = ?")
            values.append(int(camera.enable_illegal_parking))
        if camera.enable_license_plate is not None:
            assignments.append("bat_phat_hien_bien_so = ?")
            values.append(int(camera.enable_license_plate))
        if camera.is_active is not None:
            assignments.append("trang_thai_hoat_dong = ?")
            values.append(int(camera.is_active))
        if camera.model_path is not None:
            assignments.append("mo_hinh_yolo = ?")
            values.append(camera.model_path)

        if not assignments:
            return self.get_by_id(camera.id)

        assignments.append("ngay_cap_nhat = CURRENT_TIMESTAMP")
        values.append(camera.id)

        with connect() as connection:
            cursor = connection.execute(
                f"UPDATE camera SET {', '.join(assignments)} WHERE id = ?",
                values,
            )
            connection.commit()
            if cursor.rowcount == 0:
                return None

        return self.get_by_id(camera.id)

    def delete(self, camera_id: int) -> bool:
        with connect() as connection:
            cursor = connection.execute("DELETE FROM camera WHERE id = ?", (camera_id,))
            connection.commit()
            return cursor.rowcount > 0
