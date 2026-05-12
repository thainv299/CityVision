from abc import ABC, abstractmethod
from typing import List, Optional

from domain.entities.camera import Camera


class CameraRepository(ABC):
    @abstractmethod
    def get_by_id(self, camera_id: int) -> Optional[Camera]:
        pass

    @abstractmethod
    def list_all(self) -> List[Camera]:
        pass

    @abstractmethod
    def get_active_count(self) -> int:
        pass

    @abstractmethod
    def get_feature_counts(self) -> dict:
        """Returns dict like {'congestion': 1, 'illegal_parking': 2, 'license_plate': 3}"""
        pass

    @abstractmethod
    def get_recent(self, limit: int = 6) -> List[Camera]:
        pass

    @abstractmethod
    def create(self, camera: Camera) -> Camera:
        pass

    @abstractmethod
    def update(self, camera: Camera) -> Optional[Camera]:
        pass

    @abstractmethod
    def delete(self, camera_id: int) -> bool:
        pass
