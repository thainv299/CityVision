import os
from pathlib import Path

# Thư mục gốc
APP_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = APP_DIR.parent

# Thư mục thực thi
INPUTS_DIR = APP_DIR / "runtime" / "inputs"
OUTPUTS_DIR = APP_DIR / "runtime" / "outputs"
PREVIEWS_DIR = APP_DIR / "runtime" / "previews"
SAMPLES_DIR = PROJECT_ROOT / "data" / "samples"

# Cấu hình file / ML
ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".mpeg", ".mpg"}
DEFAULT_MODEL_PATH = PROJECT_ROOT / "models" / "best.pt"

# Cấu hình người dùng
VALID_ROLES = {"admin", "operator"}

# Security
SECRET_KEY = os.environ.get("CROWD_PORTAL_SECRET", "crow-detection-web-secret")
MAX_CONTENT_LENGTH = 1024 * 1024 * 1024  # 1GB

# SQL
DATABASE_PATH = APP_DIR / "portal.db"
