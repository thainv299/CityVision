from dataclasses import dataclass
from typing import List, Optional


@dataclass
class Camera:
    id: Optional[int]
    name: str
    stream_source: str = ""
    description: str = ""
    roi_points: Optional[List[List[float]]] = None
    roi_meta: Optional[dict] = None
    no_parking_points: Optional[List[List[float]]] = None
    no_park_meta: Optional[dict] = None
    enable_congestion: bool = True
    enable_illegal_parking: bool = True
    enable_license_plate: bool = True
    is_active: bool = True
    model_path: str = ""
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "stream_source": self.stream_source,
            "description": self.description,
            "roi_points": self.roi_points,
            "roi_meta": self.roi_meta,
            "no_parking_points": self.no_parking_points,
            "no_park_meta": self.no_park_meta,
            "enable_congestion": self.enable_congestion,
            "enable_illegal_parking": self.enable_illegal_parking,
            "enable_license_plate": self.enable_license_plate,
            "is_active": self.is_active,
            "model_path": self.model_path,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
