import os
import sys
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from pathlib import Path
from core.config import APP_DIR, PROJECT_ROOT
from frontend.database import init_db
from core.exceptions import http_exception_handler

from routers import web_views, api_users, api_cameras, api_jobs, api_license_plates

if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

# Khởi tạo hoặc cập nhật Database SQLite
init_db()

app = FastAPI(title="Crowd Detection API")

# Add Middlewares
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("CROWD_PORTAL_SECRET", "crow-detection-web-secret"),
    max_age=86400 * 7
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Exception Handlers
app.add_exception_handler(StarletteHTTPException, http_exception_handler)

# Static files
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")

# Mount logs directory to serve license plate images and violations
LOGS_DIR = PROJECT_ROOT / "logs"
if not LOGS_DIR.exists():
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/logs", StaticFiles(directory=str(LOGS_DIR)), name="logs")

# Include Routers
app.include_router(web_views.router)
app.include_router(api_users.router)
app.include_router(api_cameras.router)
app.include_router(api_jobs.router)
app.include_router(api_license_plates.router)
