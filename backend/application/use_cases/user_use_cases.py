from typing import Any, Dict, List

from werkzeug.security import generate_password_hash

from core.config import VALID_ROLES
from core.errors import AlreadyExistsError, ForbiddenError, NotFoundError, ValidationError
from domain.entities.user import User
from domain.repositories.user_repository import UserRepository


class UserUseCases:
    def __init__(self, user_repo: UserRepository):
        self.user_repo = user_repo

    def list_users(self) -> List[User]:
        return self.user_repo.list_all()

    def get_user(self, user_id: int) -> User:
        user = self.user_repo.get_by_id(user_id)
        if not user:
            raise NotFoundError("Không tìm thấy người dùng.")
        return user

    def _validate_payload(self, payload: Dict[str, Any], is_create: bool) -> User:
        username = str(payload.get("username", "")).strip()
        full_name = str(payload.get("full_name", "")).strip()
        role = str(payload.get("role", "operator")).strip().lower()
        password = str(payload.get("password", "")).strip()

        if is_create:
            if not username:
                raise ValidationError("Tên đăng nhập không được để trống.")
            if not full_name:
                raise ValidationError("Họ tên không được để trống.")
            if len(password) < 6:
                raise ValidationError("Mật khẩu cần tối thiểu 6 ký tự.")
                
        if role not in VALID_ROLES:
            raise ValidationError("Vai trò không hợp lệ.")

        is_active_input = payload.get("is_active")
        is_active = True if is_active_input is None else str(is_active_input).strip().lower() in {"1", "true", "yes", "on"}

        user = User(
            id=None,
            username=username,
            full_name=full_name,
            password_hash=generate_password_hash(password) if password else "",
            role=role,
            is_active=is_active
        )
        return user

    def create_user(self, payload: Dict[str, Any]) -> User:
        user = self._validate_payload(payload, is_create=True)
        if self.user_repo.get_by_username(user.username):
            raise AlreadyExistsError("Tên đăng nhập đã tồn tại.")
        return self.user_repo.create(user)

    def update_user(self, user_id: int, payload: Dict[str, Any], current_role: str) -> User:
        existing = self.get_user(user_id)
        updates = self._validate_payload(payload, is_create=False)

        # Chống update username thành tên trùng
        if updates.username and updates.username != existing.username:
            if self.user_repo.get_by_username(updates.username):
                raise AlreadyExistsError("Tên đăng nhập đã tồn tại.")

        if not updates.password_hash:
            updates.password_hash = existing.password_hash # type: ignore

        if existing.is_admin() and updates.role != "admin" and self.user_repo.count_admin() <= 1:
            raise ValidationError("Cần giữ lại ít nhất một tài khoản admin.")

        updates.id = user_id
        updated = self.user_repo.update(updates)
        if not updated:
            raise NotFoundError("Không tìm thấy người dùng.")
        return updated

    def delete_user(self, user_id: int, current_user_id: int) -> bool:
        target = self.get_user(user_id)
        if current_user_id == user_id:
            raise ValidationError("Không thể tự xóa chính mình.")
        if target.is_admin() and self.user_repo.count_admin() <= 1:
            raise ValidationError("Cần giữ lại ít nhất một tài khoản admin.")

        deleted = self.user_repo.delete(user_id)
        if not deleted:
            raise ValidationError("Không thể xóa người dùng.")
        return True
