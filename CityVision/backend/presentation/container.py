from application.use_cases.camera_use_cases import CameraUseCases
from application.use_cases.dashboard_use_cases import DashboardUseCases
from application.use_cases.job_use_cases import JobUseCases
from application.use_cases.user_use_cases import UserUseCases
from database.sqlite_camera_repo import SqliteCameraRepository
from database.sqlite_user_repo import SqliteUserRepository
from infrastructure.file_system.local_storage import LocalStorage
from infrastructure.ml.detection_bridge import YoloDetectionService


class Container:
    def __init__(self):
        # 1. Khởi tạo Repositories (Data Access)
        self.user_repo = SqliteUserRepository()
        self.camera_repo = SqliteCameraRepository()

        # 2. Khởi tạo Infrastructures (Ngoại vi)
        self.file_storage = LocalStorage()
        self.detection_service = YoloDetectionService()

        # 3. Khởi tạo Use Cases (Business Logic)
        self.user_use_cases = UserUseCases(self.user_repo)
        self.camera_use_cases = CameraUseCases(self.camera_repo)
        self.dashboard_use_cases = DashboardUseCases(self.user_repo, self.camera_repo)
        self.job_use_cases = JobUseCases(self.detection_service, self.file_storage)

    def render_template(self, request, name: str, context: dict = None):
        """Bọc TemplateResponse để tự động thêm request và current_user vào context."""
        full_context = context.copy() if context else {}
        full_context["request"] = request
        full_context["current_user"] = getattr(request.state, "current_user", None)

        # Hỗ trợ url_for('static', filename='...') của Flask sang FastAPI
        def url_for(name: str, **path_params) -> str:
            if name == "static" and "filename" in path_params:
                path_params["path"] = path_params.pop("filename")
            try:
                return str(request.url_for(name, **path_params))
            except:
                return ""
        full_context["url_for"] = url_for

        return templates.TemplateResponse(request=request, name=name, context=full_context)

# Singleton Pattern cho Container và Templates
container = Container()

from fastapi.templating import Jinja2Templates
from core.config import PROJECT_ROOT

templates = Jinja2Templates(directory=str(PROJECT_ROOT / "frontend" / "templates"))
