from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, Optional


class DetectionInterface(ABC):
    @abstractmethod
    def process_video(
        self,
        input_stream: Any = None,
        input_path: str = None,
        input_ext: str = None,
        settings: Dict[str, Any] = None,
        progress_callback: Callable[[Dict[str, Any]], Optional[Dict[str, Any]]] = None,
        pause_event: Any = None,
    ) -> Dict[str, Any]:
        """Xử lý video và gọi progress_callback mỗi khi cập nhật, hỗ trợ tạm dừng"""
        pass
