from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class User:
    id: Optional[int]
    username: str
    full_name: str
    password_hash: str
    role: str = "operator"
    is_active: bool = True
    camera_access: Dict[int, int] = field(default_factory=dict)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    def is_admin(self) -> bool:
        return self.role == "admin"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "username": self.username,
            "full_name": self.full_name,
            "role": self.role,
            "is_active": self.is_active,
            "camera_access": self.camera_access,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
