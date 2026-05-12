import os
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from werkzeug.utils import secure_filename

from core.config import ALLOWED_VIDEO_EXTENSIONS, INPUTS_DIR, OUTPUTS_DIR, PREVIEWS_DIR, PROJECT_ROOT


class LocalStorage:
    def __init__(self):
        INPUTS_DIR.mkdir(parents=True, exist_ok=True)
        OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
        PREVIEWS_DIR.mkdir(parents=True, exist_ok=True)

    def is_allowed_video(self, filename: str) -> bool:
        ext = Path(filename).suffix.lower()
        return ext in ALLOWED_VIDEO_EXTENSIONS

    def save_upload(self, file_storage, job_id: str) -> Path:
        """Lưu file upload từ request Flask"""
        from werkzeug.utils import secure_filename
        safe_name = secure_filename(str(file_storage.filename))
        target_path = INPUTS_DIR / f"{job_id}_{safe_name}"
        file_storage.save(target_path)
        return target_path

    def save_upload_fastapi(self, upload_file, job_id: str) -> Path:
        """Lưu file upload từ FastAPI UploadFile"""
        from werkzeug.utils import secure_filename
        safe_name = secure_filename(str(upload_file.filename))
        target_path = INPUTS_DIR / f"{job_id}_{safe_name}"
        with target_path.open("wb") as buffer:
            import shutil
            shutil.copyfileobj(upload_file.file, buffer)
        return target_path

    def save_preview_image(self, job_id: str, preview_jpeg: bytes) -> Path:
        """Lưu file ảnh preview tạm cho job"""
        preview_path = self.get_preview_path(job_id)
        preview_path.write_bytes(preview_jpeg)
        return preview_path
        
    def get_preview_path(self, job_id: str) -> Path:
        return PREVIEWS_DIR / f"{job_id}.jpg"

    def get_output_path(self, filename: str) -> Path:
        return OUTPUTS_DIR / filename

    def delete_output_file(self, filename: str) -> bool:
        path = self.get_output_path(filename)
        try:
            if path.exists():
                path.unlink()
                return True
        except OSError:
            pass
        return False
