from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class Job:
    id: str
    status: str = "queued"
    message: str = "Đang chờ xử lý"
    error: Optional[str] = None
    source_video: Optional[str] = None
    output_filename: Optional[str] = None
    submitted_at: Optional[float] = None
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    summary: Optional[Dict[str, Any]] = None
    progress: Dict[str, Any] = field(default_factory=dict)
    latest_frame: Optional[bytes] = None
    is_paused: bool = False
    camera_id: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "message": self.message,
            "error": self.error,
            "source_video": self.source_video,
            "output_filename": self.output_filename,
            "submitted_at": self.submitted_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "summary": self.summary,
            "progress": self.progress,
            "is_paused": self.is_paused,
            "camera_id": self.camera_id,
        }
