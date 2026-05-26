import sqlite3
from typing import List, Optional

from domain.entities.user import User
from domain.repositories.user_repository import UserRepository
from database.sqlite_db import connect


class SqliteUserRepository(UserRepository):

    def _row_to_user(self, row: sqlite3.Row) -> User:
        return User(
            id=row["id"],
            username=row["ten_dang_nhap"],
            full_name=row["ho_ten"],
            password_hash=row["mat_khau_hash"],
            role=row["vai_tro"],
            is_active=bool(row["trang_thai_hoat_dong"]),
            created_at=row["ngay_tao"],
            updated_at=row["ngay_cap_nhat"],
        )

    def get_by_id(self, user_id: int) -> Optional[User]:
        with connect() as connection:
            row = connection.execute(
                "SELECT * FROM nguoi_dung WHERE id = ?", (user_id,)
            ).fetchone()
        return self._row_to_user(row) if row else None

    def get_by_username(self, username: str) -> Optional[User]:
        with connect() as connection:
            row = connection.execute(
                "SELECT * FROM nguoi_dung WHERE ten_dang_nhap = ?", (username,)
            ).fetchone()
        return self._row_to_user(row) if row else None

    def list_all(self) -> List[User]:
        with connect() as connection:
            rows = connection.execute(
                "SELECT * FROM nguoi_dung ORDER BY ngay_tao DESC, id DESC"
            ).fetchall()
        return [self._row_to_user(row) for row in rows]

    def count_admin(self) -> int:
        with connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS total FROM nguoi_dung WHERE vai_tro = 'admin'"
            ).fetchone()
        return int(row["total"])

    def create(self, user: User) -> User:
        with connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO nguoi_dung (ten_dang_nhap, ho_ten, mat_khau_hash, vai_tro, trang_thai_hoat_dong)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    user.username,
                    user.full_name,
                    user.password_hash,
                    user.role,
                    int(user.is_active),
                ),
            )
            connection.commit()
            user_id = int(cursor.lastrowid)
        
        created = self.get_by_id(user_id)
        if not created:
            raise RuntimeError("Lỗi lưu DB khi tạo Nguoi dung")
        return created

    def update(self, user: User) -> Optional[User]:
        assignments = []
        values = []

        if user.username is not None:
            assignments.append("ten_dang_nhap = ?")
            values.append(user.username)
        if user.full_name is not None:
            assignments.append("ho_ten = ?")
            values.append(user.full_name)
        if user.password_hash is not None:
            assignments.append("mat_khau_hash = ?")
            values.append(user.password_hash)
        if user.role is not None:
            assignments.append("vai_tro = ?")
            values.append(user.role)
        if user.is_active is not None:
            assignments.append("trang_thai_hoat_dong = ?")
            values.append(int(user.is_active))

        if not assignments:
            return self.get_by_id(user.id)

        assignments.append("ngay_cap_nhat = CURRENT_TIMESTAMP")
        values.append(user.id)

        with connect() as connection:
            cursor = connection.execute(
                f"UPDATE nguoi_dung SET {', '.join(assignments)} WHERE id = ?",
                values,
            )
            connection.commit()
            if cursor.rowcount == 0:
                return None

        return self.get_by_id(user.id)

    def delete(self, user_id: int) -> bool:
        with connect() as connection:
            cursor = connection.execute("DELETE FROM nguoi_dung WHERE id = ?", (user_id,))
            connection.commit()
            return cursor.rowcount > 0
