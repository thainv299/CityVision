class AppError(Exception):
    """Lớp Exception cơ bản cho ứng dụng"""
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class ValidationError(AppError):
    """Lỗi dữ liệu đầu vào không hợp lệ"""
    def __init__(self, message: str):
        super().__init__(message, status_code=400)


class NotFoundError(AppError):
    """Lỗi không tìm thấy dữ liệu"""
    def __init__(self, message: str = "Không tìm thấy dữ liệu"):
        super().__init__(message, status_code=404)


class AlreadyExistsError(AppError):
    """Lỗi dữ liệu đã tồn tại"""
    def __init__(self, message: str = "Dữ liệu đã tồn tại"):
        super().__init__(message, status_code=409)


class UnauthorizedError(AppError):
    """Lỗi chưa đăng nhập / không hợp lệ"""
    def __init__(self, message: str = "Bạn cần đăng nhập để tiếp tục"):
        super().__init__(message, status_code=401)


class ForbiddenError(AppError):
    """Lỗi không có quyền truy cập"""
    def __init__(self, message: str = "Bạn không có quyền truy cập chức năng này"):
        super().__init__(message, status_code=403)
