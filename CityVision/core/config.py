from pathlib import Path
import threading
from concurrent.futures import ThreadPoolExecutor

PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_DIR = PROJECT_ROOT / "frontend"
DEFAULT_MODEL_PATH = PROJECT_ROOT / "models" / "best.pt"
INPUTS_DIR = APP_DIR / "runtime" / "inputs"
OUTPUTS_DIR = APP_DIR / "runtime" / "outputs"
PREVIEWS_DIR = APP_DIR / "runtime" / "previews"
ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".mpeg", ".mpg"}
VALID_ROLES = {"admin", "operator"}

from fastapi.templating import Jinja2Templates

INPUTS_DIR.mkdir(parents=True, exist_ok=True)
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
PREVIEWS_DIR.mkdir(parents=True, exist_ok=True)

templates = Jinja2Templates(directory=str(APP_DIR / "templates"))

# Khởi tạo executor và dictionary quản lý threads toàn cục
executor = ThreadPoolExecutor(max_workers=1)
job_lock = threading.Lock()
jobs = {}
