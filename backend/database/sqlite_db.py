from typing import Optional, Any, List, Dict
import sqlite3
import os
import time
import threading
from pathlib import Path
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash
import asyncio
from core.config import DATABASE_PATH


def connect() -> sqlite3.Connection:
    """Tạo kết nối mới tới CSDL SQLite"""
    db_path = Path(DATABASE_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row
    return connection


def cleanup_old_data(days_to_keep: int = 90) -> None:
    """Xóa dữ liệu lịch sử cũ hơn N ngày (Cả DB và file vật lý)"""
    # Định dạng thời gian
    cutoff_datetime = (datetime.now() - timedelta(days=days_to_keep)).strftime("%Y-%m-%d %H:%M:%S")
    cutoff_date_only = (datetime.now() - timedelta(days=days_to_keep)).strftime("%Y-%m-%d")
    
    print(f"[Database] Đang dọn dẹp dữ liệu cũ hơn {days_to_keep} ngày...")
    
    try:
        files_to_delete = []
        
        with connect() as connection:
            # 1. Thu thập đường dẫn ảnh vi phạm đỗ xe
            rows = connection.execute("SELECT duong_dan_anh FROM vi_pham_do_xe WHERE thoi_gian_vi_pham < ?", (cutoff_datetime,)).fetchall()
            for r in rows:
                if r["duong_dan_anh"]: files_to_delete.append(r["duong_dan_anh"])
                
            # 2. Thu thập đường dẫn ảnh ùn tắc
            rows = connection.execute("SELECT duong_dan_anh FROM nhat_ky_un_tac WHERE thoi_gian_bat_dau < ?", (cutoff_datetime,)).fetchall()
            for r in rows:
                if r["duong_dan_anh"]: files_to_delete.append(r["duong_dan_anh"])
                
            # 3. Thu thập đường dẫn ảnh biển số (có thể có nhiều ảnh cách nhau dấu phẩy)
            rows = connection.execute("SELECT duong_dan_anh FROM bien_so_phat_hien WHERE ngay_tao < ?", (cutoff_datetime,)).fetchall()
            for r in rows:
                if r["duong_dan_anh"]:
                    paths = r["duong_dan_anh"].split(',')
                    for p in paths:
                        files_to_delete.append(p.strip())

            # 4. Thu thập đường dẫn ảnh phương tiện di chuyển qua
            try:
                rows = connection.execute("SELECT duong_dan_anh FROM lich_su_phuong_tien WHERE thoi_gian_di_qua < ?", (cutoff_datetime,)).fetchall()
                for r in rows:
                    if r["duong_dan_anh"]:
                        files_to_delete.append(r["duong_dan_anh"])
            except Exception as e:
                pass
            
            # --- THỰC HIỆN XÓA TRONG DATABASE ---
            
            # Xóa vi phạm đỗ xe
            connection.execute("DELETE FROM vi_pham_do_xe WHERE thoi_gian_vi_pham < ?", (cutoff_datetime,))
            # Xóa nhật ký ùn tắc
            connection.execute("DELETE FROM nhat_ky_un_tac WHERE thoi_gian_bat_dau < ?", (cutoff_datetime,))
            # Xóa lịch sử phương tiện chi tiết
            connection.execute("DELETE FROM lich_su_phuong_tien WHERE thoi_gian_di_qua < ?", (cutoff_datetime,))
            # Xóa lịch sử biển số
            connection.execute("DELETE FROM bien_so_phat_hien WHERE ngay_tao < ?", (cutoff_datetime,))
            # Xóa thông báo cũ
            connection.execute("DELETE FROM thong_bao WHERE ngay_tao < ?", (cutoff_datetime,))
            # Xóa thống kê gộp cũ
            connection.execute("DELETE FROM thong_ke_giao_thong WHERE ngay_ghi_nhan < ?", (cutoff_date_only,))
            
            connection.commit()

        # --- THỰC HIỆN XÓA FILE VẬT LÝ ---
        deleted_files_count = 0
        for rel_path in set(files_to_delete): # Dùng set để tránh xóa trùng
            if not rel_path: continue
            # Đảm bảo đường dẫn là tuyệt đối từ project root
            # rel_path thường có dạng: logs/violations/... hoặc logs/plates/...
            abs_path = Path(os.getcwd()) / rel_path.replace("/", os.sep)
            
            if abs_path.exists():
                try:
                    if abs_path.is_file():
                        os.remove(abs_path)
                        deleted_files_count += 1
                    # Nếu là thư mục (trong trường hợp vi phạm lưu cả cụm), xóa cả thư mục
                    elif abs_path.is_dir():
                        import shutil
                        shutil.rmtree(abs_path)
                        deleted_files_count += 1
                except Exception as e:
                    print(f"[Database] Không thể xóa file/thư mục {abs_path}: {e}")

        # VACUUM để thu hồi dung lượng đĩa
        conn = sqlite3.connect(DATABASE_PATH)
        conn.execute("VACUUM")
        conn.close()
        
        print(f"[Database] Dọn dẹp hoàn tất: Đã xóa {deleted_files_count} file và nén CSDL thành công.")
        
    except Exception as e:
        print(f"[Database] Lỗi trong quá trình dọn dẹp định kỳ: {e}")


def delete_license_plate_record(record_id: int) -> bool:
    """Xóa một bản ghi biển số/phương tiện theo ID và xóa cả ảnh trên đĩa"""
    import os
    from pathlib import Path
    try:
        # 1. Lấy đường dẫn ảnh trước khi xóa bản ghi
        image_paths = None
        with connect() as connection:
            row = connection.execute("SELECT duong_dan_anh FROM bien_so_phat_hien WHERE id = ?", (record_id,)).fetchone()
            if row:
                image_paths = row["duong_dan_anh"]
        
        # 2. Xóa ảnh vật lý trên đĩa
        if image_paths:
            # image_paths có thể là chuỗi phân tách bởi dấu phẩy
            paths = image_paths.split(',')
            for p in paths:
                p = p.strip()
                if not p: continue
                # Chuyển đổi đường dẫn web (logs/plates/...) thành đường dẫn vật lý
                # logs/plates/2026/05/06/... -> logs/plates/2026/05/06/...
                # PROJECT_ROOT được định nghĩa ở trên là backend/
                # Nhưng thực tế logs/ nằm ở project root.
                full_path = Path(p)
                if full_path.exists():
                    try:
                        os.remove(full_path)
                        print(f"[Database] Đã xóa ảnh: {full_path}")
                    except Exception as e:
                        print(f"[Database] Không thể xóa file {full_path}: {e}")

        # 3. Xóa bản ghi trong DB
        with connect() as connection:
            connection.execute("DELETE FROM bien_so_phat_hien WHERE id = ?", (record_id,))
            connection.commit()
            return True
    except Exception as e:
        print(f"[Database] Lỗi khi xóa bản ghi {record_id}: {e}")
        return False


def init_db() -> None:
    """Khởi tạo cấu trúc bảng nếu chưa có"""
    with connect() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS nguoi_dung (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ten_dang_nhap TEXT NOT NULL UNIQUE,
                ho_ten TEXT NOT NULL,
                mat_khau_hash TEXT NOT NULL,
                vai_tro TEXT NOT NULL DEFAULT 'operator',
                trang_thai_hoat_dong INTEGER NOT NULL DEFAULT 1,
                ngay_tao TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                ngay_cap_nhat TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS camera (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ten_camera TEXT NOT NULL UNIQUE,
                nguon_phat TEXT,
                mo_ta TEXT,
                toa_do_vung_chon TEXT,
                toa_do_cam_do TEXT,
                bat_phat_hien_un_tac INTEGER NOT NULL DEFAULT 1,
                bat_phat_hien_do_sai INTEGER NOT NULL DEFAULT 1,
                bat_phat_hien_bien_so INTEGER NOT NULL DEFAULT 1,
                bat_xu_ly_ai INTEGER NOT NULL DEFAULT 1,
                trang_thai_hoat_dong INTEGER NOT NULL DEFAULT 1,
                mo_hinh_yolo TEXT,
                ngay_tao TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                ngay_cap_nhat TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS thong_ke_giao_thong (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                id_camera INTEGER NOT NULL,
                so_luong_xe INTEGER NOT NULL DEFAULT 0,
                ngay_ghi_nhan TEXT NOT NULL,
                ngay_tao TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (id_camera) REFERENCES camera(id),
                UNIQUE(id_camera, ngay_ghi_nhan)
            );

            CREATE TABLE IF NOT EXISTS vi_pham_do_xe (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                id_camera INTEGER NOT NULL,
                bien_so TEXT,
                thoi_gian_vi_pham TEXT NOT NULL,
                thoi_gian_do_giay INTEGER NOT NULL DEFAULT 0,
                thoi_gian_ket_thuc TEXT,
                thoi_gian_cap_nhat_cuoi TEXT,
                duong_dan_anh TEXT,
                da_giai_quyet INTEGER NOT NULL DEFAULT 0,
                ngay_tao TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (id_camera) REFERENCES camera(id)
            );

            CREATE TABLE IF NOT EXISTS nhat_ky_un_tac (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                id_camera INTEGER NOT NULL,
                muc_do_un_tac INTEGER NOT NULL DEFAULT 1,
                thoi_gian_bat_dau TEXT NOT NULL,
                thoi_gian_ket_thuc TEXT,
                thoi_gian_keo_dai_giay INTEGER DEFAULT 0,
                thoi_gian_cap_nhat_cuoi TEXT,
                ngay_tao TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (id_camera) REFERENCES camera(id)
            );

            CREATE TABLE IF NOT EXISTS bien_so_phat_hien (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bien_so TEXT NOT NULL,
                ngay_phat_hien TEXT NOT NULL,
                thoi_gian_phat_hien TEXT,
                so_lan_phat_hien INTEGER NOT NULL DEFAULT 1,
                do_chinh_xac_tb REAL DEFAULT 0.0,
                duong_dan_anh TEXT,
                id_camera INTEGER DEFAULT 0,
                ngay_tao TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                ngay_cap_nhat TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS lich_su_phuong_tien (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                id_camera INTEGER DEFAULT 0,
                bien_so_xe TEXT,
                loai_xe TEXT,
                thoi_gian_di_qua TEXT NOT NULL,
                duong_dan_anh TEXT,
                ngay_tao TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS cai_dat_camera (
                camera_id INTEGER PRIMARY KEY,
                confidence REAL NOT NULL DEFAULT 0.37,
                frame_skip INTEGER NOT NULL DEFAULT 2,
                congestion_threshold INTEGER NOT NULL DEFAULT 35,
                parking_violation_time INTEGER NOT NULL DEFAULT 30,
                log_retention TEXT NOT NULL DEFAULT '30_days',
                enabled_detect_labels TEXT,
                FOREIGN KEY (camera_id) REFERENCES camera(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS thong_bao (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                loai_thong_bao TEXT NOT NULL, -- 'violation', 'congestion', 'system'
                id_ban_ghi INTEGER,          -- ID của vi_pham_do_xe hoặc nhat_ky_un_tac
                tieu_de TEXT,
                noi_dung TEXT,
                duong_dan_anh TEXT,
                da_doc INTEGER NOT NULL DEFAULT 0,
                ngay_tao TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS camera_nguoi_dung (
                id_camera INTEGER NOT NULL,
                id_nguoidung INTEGER NOT NULL,
                quyen INTEGER NOT NULL DEFAULT 0,
                ngay_tao TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (id_camera, id_nguoidung),
                FOREIGN KEY (id_nguoidung) REFERENCES nguoi_dung(id) ON DELETE CASCADE,
                FOREIGN KEY (id_camera) REFERENCES camera(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS phat_hien_vu_khi (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                id_camera INTEGER NOT NULL,
                loai_vu_khi TEXT NOT NULL,
                thoi_gian_phat_hien TEXT NOT NULL,
                do_chinh_xac REAL DEFAULT 0.0,
                duong_dan_anh TEXT NOT NULL,
                da_giai_quyet INTEGER NOT NULL DEFAULT 0,
                track_id INTEGER DEFAULT -1,
                ngay_tao TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (id_camera) REFERENCES camera(id)
            );

            CREATE TABLE IF NOT EXISTS cai_dat_he_thong (
                khoa TEXT PRIMARY KEY,
                gia_tri TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS telegram_alert_dedup (
                alert_key TEXT PRIMARY KEY,
                sent_at REAL NOT NULL
            );
            
            CREATE INDEX IF NOT EXISTS idx_telegram_alert_dedup_sent_at ON telegram_alert_dedup(sent_at);

            
            CREATE INDEX IF NOT EXISTS idx_lich_su_phuong_tien_thoi_gian ON lich_su_phuong_tien(thoi_gian_di_qua);
            CREATE INDEX IF NOT EXISTS idx_lich_su_phuong_tien_camera_thoi_gian ON lich_su_phuong_tien(id_camera, thoi_gian_di_qua);
            CREATE INDEX IF NOT EXISTS idx_lich_su_phuong_tien_loai_xe ON lich_su_phuong_tien(loai_xe);
            
            CREATE INDEX IF NOT EXISTS idx_vi_pham_do_xe_thoi_gian ON vi_pham_do_xe(thoi_gian_vi_pham);
            CREATE INDEX IF NOT EXISTS idx_vi_pham_do_xe_camera_thoi_gian ON vi_pham_do_xe(id_camera, thoi_gian_vi_pham);
            
            CREATE INDEX IF NOT EXISTS idx_nhat_ky_un_tac_bat_dau ON nhat_ky_un_tac(thoi_gian_bat_dau);
            CREATE INDEX IF NOT EXISTS idx_nhat_ky_un_tac_camera_bat_dau ON nhat_ky_un_tac(id_camera, thoi_gian_bat_dau);
            
            CREATE INDEX IF NOT EXISTS idx_thong_ke_giao_thong_ngay ON thong_ke_giao_thong(ngay_ghi_nhan);
            """
        )

        # Di chuyển dữ liệu từ bảng cũ sang bảng mới (nếu bảng cũ còn tồn tại)
        try:
            # Kiểm tra xem bảng quyen_truy_cap_camera có tồn tại không
            table_exists = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='quyen_truy_cap_camera'"
            ).fetchone()
            if table_exists:
                # Migrate data (mặc định quyền 1 cho dữ liệu cũ)
                connection.execute("""
                    INSERT OR IGNORE INTO camera_nguoi_dung (id_camera, id_nguoidung, quyen, ngay_tao)
                    SELECT id_camera, id_nguoi_dung, 1, ngay_tao FROM quyen_truy_cap_camera
                """)
                # Xóa bảng cũ
                connection.execute("DROP TABLE quyen_truy_cap_camera")
        except Exception as e:
            print(f"[Database] Lỗi khi di chuyển dữ liệu: {e}")

        # Thêm cột thoi_gian_ket_thuc vào bảng vi_pham_do_xe nếu chưa có
        try:
            connection.execute("SELECT thoi_gian_ket_thuc FROM vi_pham_do_xe LIMIT 1")
        except sqlite3.OperationalError:
            try:
                connection.execute("ALTER TABLE vi_pham_do_xe ADD COLUMN thoi_gian_ket_thuc TEXT")
                print("[Database] Đã thêm cột thoi_gian_ket_thuc vào bảng vi_pham_do_xe")
            except Exception as e:
                print(f"[Database] Lỗi khi thêm cột thoi_gian_ket_thuc: {e}")

        # Thêm cột thoi_gian_cap_nhat_cuoi vào bảng vi_pham_do_xe nếu chưa có
        try:
            connection.execute("SELECT thoi_gian_cap_nhat_cuoi FROM vi_pham_do_xe LIMIT 1")
        except sqlite3.OperationalError:
            try:
                connection.execute("ALTER TABLE vi_pham_do_xe ADD COLUMN thoi_gian_cap_nhat_cuoi TEXT")
                print("[Database] Đã thêm cột thoi_gian_cap_nhat_cuoi vào bảng vi_pham_do_xe")
            except Exception as e:
                print(f"[Database] Lỗi khi thêm cột thoi_gian_cap_nhat_cuoi cho vi_pham_do_xe: {e}")

        # Thêm cột thoi_gian_cap_nhat_cuoi vào bảng nhat_ky_un_tac nếu chưa có
        try:
            connection.execute("SELECT thoi_gian_cap_nhat_cuoi FROM nhat_ky_un_tac LIMIT 1")
        except sqlite3.OperationalError:
            try:
                connection.execute("ALTER TABLE nhat_ky_un_tac ADD COLUMN thoi_gian_cap_nhat_cuoi TEXT")
            except Exception as e:
                print(f"[Database] Lỗi khi thêm cột thoi_gian_cap_nhat_cuoi cho nhat_ky_un_tac: {e}")

        camera_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(camera)").fetchall()
        }
        if "bat_phat_hien_bien_so" not in camera_columns:
            connection.execute(
                "ALTER TABLE camera ADD COLUMN bat_phat_hien_bien_so INTEGER NOT NULL DEFAULT 1"
            )
        if "bat_xu_ly_ai" not in camera_columns:
            connection.execute(
                "ALTER TABLE camera ADD COLUMN bat_xu_ly_ai INTEGER NOT NULL DEFAULT 1"
            )
        if "mo_hinh_yolo" not in camera_columns:
            connection.execute(
                "ALTER TABLE camera ADD COLUMN mo_hinh_yolo TEXT"
            )
        if "nguoi_tao_id" not in camera_columns:
            connection.execute(
                "ALTER TABLE camera ADD COLUMN nguoi_tao_id INTEGER DEFAULT NULL"
            )

        # Cột id_camera trong bảng biển số
        plate_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(bien_so_phat_hien)").fetchall()
        }
        if "id_camera" not in plate_columns:
            connection.execute(
                "ALTER TABLE bien_so_phat_hien ADD COLUMN id_camera INTEGER DEFAULT 0"
            )

        # Cột duong_dan_anh trong bảng nhat_ky_un_tac
        congestion_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(nhat_ky_un_tac)").fetchall()
        }
        if "duong_dan_anh" not in congestion_columns:
            connection.execute(
                "ALTER TABLE nhat_ky_un_tac ADD COLUMN duong_dan_anh TEXT"
            )

        # Cột bat_phat_hien_vu_khi trong bảng camera
        camera_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(camera)").fetchall()
        }
        if "bat_phat_hien_vu_khi" not in camera_columns:
            connection.execute(
                "ALTER TABLE camera ADD COLUMN bat_phat_hien_vu_khi INTEGER NOT NULL DEFAULT 1"
            )

        # Cột track_id trong bảng phat_hien_vu_khi
        wp_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(phat_hien_vu_khi)").fetchall()
        }
        if "track_id" not in wp_columns:
            connection.execute(
                "ALTER TABLE phat_hien_vu_khi ADD COLUMN track_id INTEGER DEFAULT -1"
            )

        # Cột duong_dan_anh trong bảng lich_su_phuong_tien
        veh_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(lich_su_phuong_tien)").fetchall()
        }
        if "duong_dan_anh" not in veh_columns:
            connection.execute(
                "ALTER TABLE lich_su_phuong_tien ADD COLUMN duong_dan_anh TEXT"
            )

        # Cột enabled_detect_labels trong cai_dat_camera
        cai_dat_cam_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(cai_dat_camera)").fetchall()
        }
        if "enabled_detect_labels" not in cai_dat_cam_columns:
            connection.execute(
                "ALTER TABLE cai_dat_camera ADD COLUMN enabled_detect_labels TEXT"
            )


        # Di chuyển hoặc khởi tạo cấu hình camera mặc định
        connection.execute(
            """
            INSERT OR IGNORE INTO cai_dat_camera (camera_id, confidence, frame_skip, congestion_threshold, parking_violation_time, log_retention)
            SELECT id, 0.37, 2, 35, 30, '30_days' FROM camera;
            """
        )

        connection.execute(
            """
            INSERT OR IGNORE INTO nguoi_dung (ten_dang_nhap, ho_ten, mat_khau_hash, vai_tro, trang_thai_hoat_dong)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "admin",
                "Quản trị hệ thống",
                generate_password_hash("Admin@123"),
                "admin",
                1,
            ),
        )

        connection.execute(
            """
            INSERT OR IGNORE INTO cai_dat_he_thong (khoa, gia_tri)
            VALUES ('log_retention', '30_days')
            """
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO cai_dat_he_thong (khoa, gia_tri)
            VALUES ('telegram_notifications', 'true')
            """
        )
        connection.commit()





def get_illegal_parking_violations(limit: int = 30, offset: int = 0, filter_type: str = None, date: str = None, hour: str = None, camera_id: int = None, record_id: int = None, allowed_camera_ids: list = None) -> list:
    """Lấy danh sách xe đỗ sai có phân trang và bộ lọc"""
    query = """
        SELECT pv.id, pv.id_camera as camera_id, pv.bien_so as license_plate, pv.thoi_gian_vi_pham as violation_time,
               pv.thoi_gian_ket_thuc as end_time, pv.duong_dan_anh as frame_path, pv.da_giai_quyet as is_resolved,
               pv.ngay_tao as created_at, c.ten_camera as camera_name
        FROM vi_pham_do_xe pv
        LEFT JOIN camera c ON pv.id_camera = c.id
    """
    conditions = []
    params = []

    if allowed_camera_ids is not None:
        if not allowed_camera_ids:
            return []
        placeholders = ','.join('?' * len(allowed_camera_ids))
        conditions.append(f"pv.id_camera IN ({placeholders})")
        params.extend(allowed_camera_ids)

    if filter_type == "has_plate":
        conditions.append("(pv.bien_so IS NOT NULL AND pv.bien_so != '' AND pv.bien_so NOT LIKE '%Không%' AND pv.bien_so NOT LIKE 'ID_%')")
    elif filter_type == "no_plate":
        conditions.append("(pv.bien_so IS NULL OR pv.bien_so = '' OR pv.bien_so LIKE '%Không%' OR pv.bien_so LIKE 'ID_%')")

    if date:
        conditions.append("DATE(pv.thoi_gian_vi_pham) = ?")
        params.append(date)

    if hour:
        conditions.append("strftime('%H', pv.thoi_gian_vi_pham) = ?")
        params.append(hour.zfill(2))

    if camera_id is not None:
        conditions.append("pv.id_camera = ?")
        params.append(camera_id)
    
    if record_id is not None:
        conditions.append("pv.id = ?")
        params.append(record_id)
        
    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    query += " ORDER BY pv.da_giai_quyet ASC, pv.thoi_gian_vi_pham DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    with connect() as connection:
        rows = connection.execute(query, params).fetchall()
    return [dict(row) for row in rows]

def get_illegal_parking_count(start_date: str = None, end_date: str = None, camera_ids: list = None) -> int:
    """Lấy tổng số vi phạm đỗ xe trong khoảng thời gian"""
    query = "SELECT COUNT(*) as total FROM vi_pham_do_xe"
    params = []
    conditions = []
    
    if camera_ids is not None:
        if not camera_ids:
            return 0
        placeholders = ','.join('?' * len(camera_ids))
        conditions.append(f"id_camera IN ({placeholders})")
        params.extend(camera_ids)
    
    if start_date:
        conditions.append("DATE(thoi_gian_vi_pham) >= ?")
        params.append(start_date)
    if end_date:
        conditions.append("DATE(thoi_gian_vi_pham) <= ?")
        params.append(end_date)
    
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
        
    with connect() as connection:
        row = connection.execute(query, params).fetchone()
    return row["total"] if row and row["total"] else 0

def resolve_parking_violation(violation_id: int) -> bool:
    """Đánh dấu vi phạm đỗ xe đã được giải quyết"""
    with connect() as connection:
        cursor = connection.execute(
            "UPDATE vi_pham_do_xe SET da_giai_quyet = 1 WHERE id = ?",
            (violation_id,)
        )
        connection.commit()
        return cursor.rowcount > 0


def _levenshtein_distance(s1: str, s2: str) -> int:
    """Tính khoảng cách Levenshtein giữa 2 chuỗi để phát hiện biến thể biển số đọc sai 1 ký tự."""
    if len(s1) < len(s2):
        return _levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)
    prev = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        curr = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = prev[j + 1] + 1
            deletions = curr[j] + 1
            substitutions = prev[j] + (c1 != c2)
            curr.append(min(insertions, deletions, substitutions))
        prev = curr
    return prev[-1]


def _parse_datetime_safe(dt_str):
    """Parse an toàn chuỗi ngày giờ sang đối tượng datetime."""
    if not dt_str:
        return None
    from datetime import datetime
    if isinstance(dt_str, datetime):
        return dt_str
    s = str(dt_str).strip()
    try:
        return datetime.fromisoformat(s)
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:19], fmt)
        except Exception:
            continue
    return None


# --- PHÁT HIỆN VŨ KHÍ ---
def log_weapon_detection(camera_id: int, loai_vu_khi: str, thoi_gian_phat_hien: str = None, do_chinh_xac: float = 0.0, frame_path: str = None, track_id: int = -1) -> int:
    """Lưu nhật ký phát hiện vũ khí vào CSDL theo dõi riêng biệt từng đối tượng tracking ID"""
    from datetime import datetime
    if not thoi_gian_phat_hien:
        thoi_gian_phat_hien = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cur_dt = _parse_datetime_safe(thoi_gian_phat_hien) or datetime.now()

    with connect() as connection:
        # BẢO VỆ TẦNG DATABASE: Phân biệt theo track_id
        # - Nếu cùng track_id trên cùng camera trong vòng 15 giây -> Tái sử dụng ID cũ
        # - Nếu là đối tượng vũ khí MỚI (track_id khác) -> Luôn ghi nhận và cảnh báo mới!
        if track_id is not None and track_id != -1:
            cursor = connection.execute(
                """
                SELECT id, loai_vu_khi, thoi_gian_phat_hien, track_id
                FROM phat_hien_vu_khi
                WHERE id_camera = ? AND loai_vu_khi = ? AND track_id = ?
                ORDER BY id DESC LIMIT 1
                """,
                (camera_id, loai_vu_khi, track_id)
            )
        else:
            cursor = connection.execute(
                """
                SELECT id, loai_vu_khi, thoi_gian_phat_hien, track_id
                FROM phat_hien_vu_khi
                WHERE id_camera = ? AND loai_vu_khi = ?
                ORDER BY id DESC LIMIT 1
                """,
                (camera_id, loai_vu_khi)
            )
        prev_row = cursor.fetchone()
        if prev_row:
            prev_dt = _parse_datetime_safe(prev_row["thoi_gian_phat_hien"])
            if prev_dt:
                diff_sec = abs((cur_dt - prev_dt).total_seconds())
                if track_id is not None and track_id != -1 and prev_row["track_id"] == track_id and diff_sec <= 15.0:
                    print(f"[Weapon DB Guard] Bỏ qua ghi đúp vũ khí '{loai_vu_khi}' cùng track #{track_id} ({diff_sec:.1f}s, ID: {prev_row['id']})")
                    return prev_row["id"]
                elif (track_id is None or track_id == -1) and diff_sec <= 5.0:
                    return prev_row["id"]

        frame_path_clean = frame_path or "/static/img/no_image.png"
        query = """
            INSERT INTO phat_hien_vu_khi (id_camera, loai_vu_khi, thoi_gian_phat_hien, do_chinh_xac, duong_dan_anh, track_id)
            VALUES (?, ?, ?, ?, ?, ?)
        """
        cursor = connection.execute(query, (camera_id, loai_vu_khi, thoi_gian_phat_hien, do_chinh_xac, frame_path_clean, track_id if track_id is not None else -1))
        connection.commit()
        return cursor.lastrowid

def get_weapon_detections(limit: int = 30, offset: int = 0, weapon_type: str = None, date: str = None, hour: str = None, camera_id: int = None, record_id: int = None, allowed_camera_ids: list = None) -> list:
    """Lấy danh sách sự kiện phát hiện vũ khí có phân trang và bộ lọc"""
    query = """
        SELECT vk.id, vk.id_camera as camera_id, vk.loai_vu_khi as weapon_type, vk.thoi_gian_phat_hien as detection_time,
               vk.do_chinh_xac as confidence, vk.duong_dan_anh as frame_path, vk.da_giai_quyet as is_resolved,
               vk.ngay_tao as created_at, c.ten_camera as camera_name
        FROM phat_hien_vu_khi vk
        LEFT JOIN camera c ON vk.id_camera = c.id
    """
    conditions = []
    params = []

    if allowed_camera_ids is not None:
        if not allowed_camera_ids:
            return []
        placeholders = ','.join('?' * len(allowed_camera_ids))
        conditions.append(f"vk.id_camera IN ({placeholders})")
        params.extend(allowed_camera_ids)

    if weapon_type:
        conditions.append("vk.loai_vu_khi = ?")
        params.append(weapon_type)

    if date:
        conditions.append("DATE(vk.thoi_gian_phat_hien) = ?")
        params.append(date)

    if hour:
        conditions.append("strftime('%H', vk.thoi_gian_phat_hien) = ?")
        params.append(hour.zfill(2))

    if camera_id is not None:
        conditions.append("vk.id_camera = ?")
        params.append(camera_id)
    
    if record_id is not None:
        conditions.append("vk.id = ?")
        params.append(record_id)
        
    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    query += " ORDER BY vk.da_giai_quyet ASC, vk.thoi_gian_phat_hien DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    with connect() as connection:
        rows = connection.execute(query, params).fetchall()
    return [dict(row) for row in rows]

def resolve_weapon_detection(record_id: int) -> bool:
    """Đánh dấu sự kiện phát hiện vũ khí đã giải quyết"""
    with connect() as connection:
        cursor = connection.execute(
            "UPDATE phat_hien_vu_khi SET da_giai_quyet = 1 WHERE id = ?",
            (record_id,)
        )
        connection.commit()
        return cursor.rowcount > 0

def delete_weapon_detection(record_id: int) -> bool:
    """Xóa sự kiện phát hiện vũ khí"""
    with connect() as connection:
        cursor = connection.execute(
            "DELETE FROM phat_hien_vu_khi WHERE id = ?",
            (record_id,)
        )
        connection.commit()
        return cursor.rowcount > 0


def get_congestion_count(start_date: str = None, end_date: str = None, camera_ids: list = None) -> int:
    """Lấy tổng số lần tắc nghẽn trong khoảng thời gian"""
    query = "SELECT COUNT(*) as total FROM nhat_ky_un_tac"
    params = []
    conditions = []
    
    if camera_ids is not None:
        if not camera_ids:
            return 0
        placeholders = ','.join('?' * len(camera_ids))
        conditions.append(f"id_camera IN ({placeholders})")
        params.extend(camera_ids)
    
    if start_date:
        conditions.append("DATE(thoi_gian_bat_dau) >= ?")
        params.append(start_date)
    if end_date:
        conditions.append("DATE(thoi_gian_bat_dau) <= ?")
        params.append(end_date)
    
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
        
    with connect() as connection:
        row = connection.execute(query, params).fetchone()
    return row["total"] if row and row["total"] else 0

def get_congestion_history(limit: int = 30, offset: int = 0, level: int = None, date: str = None, hour: str = None, camera_id: int = None, record_id: int = None, allowed_camera_ids: list = None) -> list:    
    """Lấy lịch sử ùn tắc có phân trang và bộ lọc"""
    query = """
        SELECT n.id, n.id_camera as camera_id, n.muc_do_un_tac as congestion_level,
               n.thoi_gian_bat_dau as start_time, n.thoi_gian_ket_thuc as end_time,
               n.thoi_gian_keo_dai_giay as duration_seconds, n.duong_dan_anh as image_path,
               c.ten_camera as camera_name
        FROM nhat_ky_un_tac n
        LEFT JOIN camera c ON n.id_camera = c.id
    """
    conditions = []
    params = []

    if allowed_camera_ids is not None:
        if not allowed_camera_ids:
            return []
        placeholders = ','.join('?' * len(allowed_camera_ids))
        conditions.append(f"n.id_camera IN ({placeholders})")
        params.extend(allowed_camera_ids)

    if level is not None:
        conditions.append("n.muc_do_un_tac = ?")
        params.append(level)

    if date:
        conditions.append("DATE(n.thoi_gian_bat_dau) = ?")
        params.append(date)

    if hour:
        conditions.append("strftime('%H', n.thoi_gian_bat_dau) = ?")
        params.append(hour.zfill(2))

    if camera_id is not None:
        conditions.append("n.id_camera = ?")
        params.append(camera_id)
    
    if record_id is not None:
        conditions.append("n.id = ?")
        params.append(record_id)

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    query += " ORDER BY n.thoi_gian_bat_dau DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    with connect() as connection:
        rows = connection.execute(query, params).fetchall()
    return [dict(row) for row in rows]

def get_daily_vehicle_stats(start_date: str = None, end_date: str = None, limit: int = 30, camera_ids: list = None) -> list:
    """Lấy thống kê lưu lượng xe hàng ngày, có hỗ trợ lọc theo khoảng thời gian và camera"""
    query = "SELECT ngay_ghi_nhan as date, SUM(so_luong_xe) as count FROM thong_ke_giao_thong"
    params = []
    conditions = []
    
    if camera_ids is not None:
        if not camera_ids:
            return []
        placeholders = ','.join('?' * len(camera_ids))
        conditions.append(f"id_camera IN ({placeholders})")
        params.extend(camera_ids)
    
    if start_date:
        conditions.append("ngay_ghi_nhan >= ?")
        params.append(start_date)
    if end_date:
        conditions.append("ngay_ghi_nhan <= ?")
        params.append(end_date)
    
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    
    query += " GROUP BY ngay_ghi_nhan ORDER BY ngay_ghi_nhan DESC LIMIT ?"
    params.append(limit)
    
    with connect() as connection:
        rows = connection.execute(query, params).fetchall()
    return [dict(row) for row in reversed(rows)]

def get_latest_violations(limit: int = 5, camera_ids: list = None) -> list:
    """Lấy danh sách vi phạm mới nhất"""
    query = """
        SELECT pv.id, pv.bien_so as license_plate, pv.thoi_gian_vi_pham as time, 
               c.ten_camera as camera_name
        FROM vi_pham_do_xe pv
        LEFT JOIN camera c ON pv.id_camera = c.id
    """
    params = []
    
    if camera_ids is not None:
        if not camera_ids:
            return []
        placeholders = ','.join('?' * len(camera_ids))
        query += f" WHERE pv.id_camera IN ({placeholders})"
        params.extend(camera_ids)
    
    query += " ORDER BY pv.thoi_gian_vi_pham DESC LIMIT ?"
    params.append(limit)
    
    with connect() as connection:
        rows = connection.execute(query, params).fetchall()
    return [dict(row) for row in rows]

# Danh sách queues lắng nghe thông báo thời gian thực phục vụ SSE
active_sse_queues = []
main_loop = None

def broadcast_notification(notification_data: dict):
    """Gửi thông báo mới tức thời tới tất cả các kết nối SSE đang hoạt động, tương thích đa luồng"""
    global main_loop
    if main_loop is not None:
        for queue in list(active_sse_queues):
            try:
                main_loop.call_soon_threadsafe(queue.put_nowait, notification_data)
            except Exception as e:
                pass
    else:
        # Fallback trong trường hợp main_loop chưa được gán
        for queue in list(active_sse_queues):
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.call_soon_threadsafe(queue.put_nowait, notification_data)
                else:
                    queue.put_nowait(notification_data)
            except Exception:
                try:
                    queue.put_nowait(notification_data)
                except Exception:
                    pass

def create_system_notification(title: str, content: str, type_name: str = "system") -> None:
    """Tạo và broadcast thông báo hệ thống (với chống ghi đúp trong vòng 10s)."""
    from datetime import datetime
    with connect() as connection:
        cursor = connection.execute(
            """
            SELECT id, tieu_de, noi_dung, (strftime('%s', 'now') - strftime('%s', ngay_tao)) as diff_sec
            FROM thong_bao
            WHERE loai_thong_bao = ? AND tieu_de = ?
            ORDER BY id DESC LIMIT 1
            """,
            (type_name, title)
        )
        prev_row = cursor.fetchone()
        if prev_row:
            diff_sec = prev_row["diff_sec"]
            if diff_sec is not None and abs(diff_sec) <= 10.0 and prev_row["noi_dung"] == content:
                return

        cursor = connection.execute(
            """
            INSERT INTO thong_bao (loai_thong_bao, tieu_de, noi_dung, duong_dan_anh)
            VALUES (?, ?, ?, ?)
            """,
            (type_name, title, content, "/static/img/model.png")
        )
        
        cursor_notif = connection.execute(
            """
            SELECT id, loai_thong_bao as type, id_ban_ghi, tieu_de as title, noi_dung, duong_dan_anh as image, ngay_tao as time
            FROM thong_bao
            WHERE id = (SELECT last_insert_rowid())
            """
        )
        notif_row = dict(cursor_notif.fetchone())
        broadcast_notification(notif_row)

def get_unread_notifications(limit: int = 10) -> dict:
    """Lấy danh sách thông báo chưa đọc từ bảng thong_bao tập trung."""
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT id, loai_thong_bao as type, id_ban_ghi, tieu_de as title, noi_dung, duong_dan_anh as image, ngay_tao as time
            FROM thong_bao
            WHERE da_doc = 0
            ORDER BY ngay_tao DESC
            LIMIT ?
            """, (limit,)
        ).fetchall()
        
        count = connection.execute("SELECT COUNT(*) as c FROM thong_bao WHERE da_doc = 0").fetchone()["c"]
        
        return {
            "unread_count": count,
            "items": [dict(row) for row in rows]
        }

def mark_notification_as_read(notif_type: str, record_id: int) -> bool:
    """Đánh dấu thông báo là đã đọc trong bảng thong_bao tập trung"""
    # Lưu ý: record_id ở đây có thể là ID của bảng thong_bao hoặc ID bản ghi gốc tùy logic gọi
    # Để tương thích với frontend click: urlPrefix + n.id -> n.id là id của thong_bao
    with connect() as connection:
        cursor = connection.execute("UPDATE thong_bao SET da_doc = 1 WHERE id = ?", (record_id,))
        connection.commit()
        return cursor.rowcount > 0


def mark_all_notifications_as_read() -> bool:
    """Đánh dấu tất cả thông báo là đã đọc trong bảng thong_bao tập trung"""
    with connect() as connection:
        connection.execute("UPDATE thong_bao SET da_doc = 1 WHERE da_doc = 0")
        connection.commit()
        return True


def get_total_vehicle_count(start_date: str = None, end_date: str = None, camera_ids: list = None) -> int:
    """Lấy tổng số xe đi qua trực tiếp từ bảng lịch sử (loại bỏ person và license_plate)"""
    query = "SELECT COUNT(*) as total FROM lich_su_phuong_tien WHERE loai_xe NOT IN ('person', 'license_plate')"
    params = []
    
    if camera_ids is not None:
        if not camera_ids:
            return 0
        placeholders = ','.join('?' * len(camera_ids))
        query += f" AND id_camera IN ({placeholders})"
        params.extend(camera_ids)
    
    if start_date or end_date:
        if start_date:
            query += " AND thoi_gian_di_qua >= ?"
            params.append(f"{start_date} 00:00:00")
        if end_date:
            query += " AND thoi_gian_di_qua <= ?"
            params.append(f"{end_date} 23:59:59")
        
    with connect() as connection:
        row = connection.execute(query, params).fetchone()
    return int(row["total"]) if row["total"] else 0

def get_vehicle_type_distribution(start_date: str = None, end_date: str = None, camera_ids: list = None) -> list:
    """Lấy tỷ lệ các loại phương tiện trong khoảng thời gian xác định (loại bỏ person và license_plate)"""
    query = "SELECT loai_xe as type, COUNT(*) as count FROM lich_su_phuong_tien WHERE loai_xe NOT IN ('person', 'license_plate')"
    params = []
    
    if camera_ids is not None:
        if not camera_ids:
            return []
        placeholders = ','.join('?' * len(camera_ids))
        query += f" AND id_camera IN ({placeholders})"
        params.extend(camera_ids)
    
    if start_date or end_date:
        if start_date:
            query += " AND thoi_gian_di_qua >= ?"
            params.append(f"{start_date} 00:00:00")
        if end_date:
            query += " AND thoi_gian_di_qua <= ?"
            params.append(f"{end_date} 23:59:59")
    
    query += " GROUP BY loai_xe ORDER BY count DESC"
    
    with connect() as connection:
        rows = connection.execute(query, params).fetchall()
    return [dict(row) for row in rows]

def get_traffic_report_by_camera(start_date: str = None, end_date: str = None, camera_ids: list = None) -> list:
    """Lấy số lượng phương tiện theo từng camera và loại xe (phục vụ báo cáo PDF).
    Trả về: [{"camera_id": 1, "camera_name": "...", "description": "...", "vehicle_type": "car", "count": 120}, ...]
    """
    query = """
        SELECT l.id_camera as camera_id, c.ten_camera as camera_name, IFNULL(c.mo_ta, '') as description,
               l.loai_xe as vehicle_type, COUNT(*) as count
        FROM lich_su_phuong_tien l
        LEFT JOIN camera c ON l.id_camera = c.id
        WHERE l.loai_xe NOT IN ('person', 'license_plate')
    """
    params = []

    if camera_ids is not None:
        if not camera_ids:
            return []
        placeholders = ','.join('?' * len(camera_ids))
        query += f" AND l.id_camera IN ({placeholders})"
        params.extend(camera_ids)

    if start_date:
        query += " AND l.thoi_gian_di_qua >= ?"
        params.append(f"{start_date} 00:00:00")
    if end_date:
        query += " AND l.thoi_gian_di_qua <= ?"
        params.append(f"{end_date} 23:59:59")

    query += " GROUP BY l.id_camera, l.loai_xe ORDER BY l.id_camera, count DESC"

    with connect() as connection:
        rows = connection.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def log_vehicle_count(camera_id: int, vehicle_type: Any = "vehicle", count: int = 1) -> None:
    """Ghi nhận số lượng xe theo loại và ngày (loại bỏ người và biển số)"""
    if isinstance(vehicle_type, int):
        count = vehicle_type
        vehicle_type = "vehicle"

    if vehicle_type in ['person', 'license_plate']:
        return
        
    from datetime import datetime
    recorded_date = datetime.now().strftime("%Y-%m-%d")
    
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO thong_ke_giao_thong (id_camera, so_luong_xe, ngay_ghi_nhan)
            VALUES (?, ?, ?)
            ON CONFLICT(id_camera, ngay_ghi_nhan) 
            DO UPDATE SET so_luong_xe = so_luong_xe + EXCLUDED.so_luong_xe
            """,
            (camera_id, count, recorded_date)
        )
        connection.commit()


def log_parking_violation(camera_id: int, license_plate: str = None, violation_time: str = None, duration: int = 0, frame_path: str = None) -> int:
    """Ghi lại vi phạm đỗ xe với bảo vệ phân biệt chính xác từng xe, cho phép nhiều xe đỗ cùng lúc và xe mới đến sau khi xe cũ đi"""
    from datetime import datetime
    if violation_time is None:
        violation_time = datetime.now().isoformat()
    
    # Đảm bảo đường dẫn lưu vào DB là tương đối và đúng thư mục logs/violations/
    if frame_path and "runtime/violations/" in frame_path:
        frame_path = frame_path.replace("runtime/violations/", "logs/violations/")

    cur_dt = _parse_datetime_safe(violation_time) or datetime.now()
    cur_plate = (str(license_plate).strip() if license_plate else "").upper()
    is_real_plate = bool(cur_plate and not cur_plate.startswith("ID_") and cur_plate != "KHÔNG BIỂN SỐ")
    
    with connect() as connection:
        # BẢO VỆ TẦNG DATABASE:
        # Chỉ coi là trùng lặp nếu đúng là CÙNG MỘT XE (cùng biển số thực hoặc cùng Track ID).
        # Tuyệt đối không chặn xe khác đỗ cùng lúc hoặc xe mới tới sau khi xe cũ đã rời đi!
        cursor = connection.execute(
            """
            SELECT id, bien_so, thoi_gian_vi_pham, thoi_gian_ket_thuc, thoi_gian_cap_nhat_cuoi, duong_dan_anh
            FROM vi_pham_do_xe
            WHERE id_camera = ?
            ORDER BY id DESC LIMIT 15
            """,
            (camera_id,)
        )
        recent_rows = cursor.fetchall()
        for row in recent_rows:
            prev_id = row["id"]
            prev_plate = (str(row["bien_so"]).strip() if row["bien_so"] else "").upper()
            prev_end = row["thoi_gian_ket_thuc"]
            prev_update = row["thoi_gian_cap_nhat_cuoi"] or row["thoi_gian_vi_pham"]
            
            diff_sec = 999999.0
            prev_dt = _parse_datetime_safe(prev_update)
            if prev_dt:
                diff_sec = abs((cur_dt - prev_dt).total_seconds())

            prev_is_real = bool(prev_plate and not prev_plate.startswith("ID_") and prev_plate != "KHÔNG BIỂN SỐ")

            # Xác định xem có đúng là CÙNG MỘT XE hay không:
            is_same_vehicle = False
            if is_real_plate and prev_is_real:
                # Cả 2 đều có biển số thật -> Phải trùng biển số (hoặc biến thể OCR <= 1 ký tự)
                if cur_plate == prev_plate or _levenshtein_distance(cur_plate, prev_plate) <= 1:
                    is_same_vehicle = True
            elif not is_real_plate and not prev_is_real:
                # Cả 2 đều là Track ID tạm -> Chỉ cùng xe nếu trùng khớp ID_...
                if cur_plate and prev_plate and cur_plate == prev_plate:
                    is_same_vehicle = True
                elif not cur_plate and not prev_plate and diff_sec <= 3.0:
                    is_same_vehicle = True

            # 1. Nếu đúng là CÙNG MỘT XE và vi phạm đó VẪN ĐANG DIỄN RA (thoi_gian_ket_thuc IS NULL):
            if is_same_vehicle and prev_end is None:
                print(f"[Parking DB Guard] Cùng xe '{cur_plate}' đang diễn ra vi phạm #{prev_id}, cập nhật heartbeat.")
                connection.execute(
                    "UPDATE vi_pham_do_xe SET thoi_gian_cap_nhat_cuoi = ?, duong_dan_anh = COALESCE(?, duong_dan_anh) WHERE id = ?",
                    (violation_time, frame_path, prev_id)
                )
                connection.commit()
                return prev_id

            # 2. Nếu đúng là CÙNG XE vừa kết thúc nhưng bị gọi lặp cực nhanh (<= 3s, race condition giữa các luồng):
            if is_same_vehicle and diff_sec <= 3.0:
                print(f"[Parking DB Guard] Bỏ qua ghi đúp cùng xe '{cur_plate}' vừa ghi cách đây {diff_sec:.1f}s (ID: {prev_id})")
                return prev_id

        # Nếu là XE KHÁC (khác biển số, khác track ID, hoặc xe mới tới sau khi xe cũ đã đi):
        # -> Luôn tạo bản ghi vi phạm mới!
        cursor = connection.execute(
            """
            INSERT INTO vi_pham_do_xe (id_camera, bien_so, thoi_gian_vi_pham, thoi_gian_do_giay, duong_dan_anh, thoi_gian_cap_nhat_cuoi)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (camera_id, license_plate, violation_time, duration, frame_path, violation_time)
        )
        violation_id = cursor.lastrowid
        
        # Thêm vào bảng thong_bao tập trung
        connection.execute(
            """
            INSERT INTO thong_bao (loai_thong_bao, id_ban_ghi, tieu_de, noi_dung, duong_dan_anh)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                'violation', 
                violation_id, 
                license_plate if license_plate else "Không biển số",
                f"Phát hiện vi phạm đỗ xe tại Camera {camera_id}",
                frame_path
            )
        )
        connection.commit()

        # Query và broadcast thời gian thực qua SSE
        cursor_notif = connection.execute(
            """
            SELECT id, loai_thong_bao as type, id_ban_ghi, tieu_de as title, noi_dung, duong_dan_anh as image, ngay_tao as time
            FROM thong_bao
            WHERE id = (SELECT last_insert_rowid())
            """
        )
        notif_row = dict(cursor_notif.fetchone())
        broadcast_notification(notif_row)
        
        return violation_id

def update_violation_end_time(violation_id: int, end_time: str = None) -> None:
    """Cập nhật thời gian kết thúc dừng đỗ cho một vi phạm"""
    from datetime import datetime
    if end_time is None:
        end_time = datetime.now().isoformat()
        
    with connect() as connection:
        connection.execute(
            """
            UPDATE vi_pham_do_xe 
            SET thoi_gian_ket_thuc = ?
            WHERE id = ?
            """,
            (end_time, violation_id)
        )
        connection.commit()


def log_congestion(camera_id: int, level: int = 1, start_time: str = None, duong_dan_anh: str = None) -> int:
    """Ghi lại sự kiện tắc nghẽn với bảo vệ chống trùng lặp tuyệt đối ở tầng CSDL"""
    from datetime import datetime
    if start_time is None:
        start_time = datetime.now().isoformat()

    cur_dt = _parse_datetime_safe(start_time) or datetime.now()
    
    with connect() as connection:
        # BẢO VỆ TẦNG DATABASE: Chống ghi đúp nhật ký ùn tắc
        cursor = connection.execute(
            """
            SELECT id, muc_do_un_tac, thoi_gian_bat_dau, thoi_gian_ket_thuc, thoi_gian_cap_nhat_cuoi
            FROM nhat_ky_un_tac
            WHERE id_camera = ?
            ORDER BY id DESC LIMIT 5
            """,
            (camera_id,)
        )
        recent_rows = cursor.fetchall()
        for row in recent_rows:
            prev_id = row["id"]
            prev_level = row["muc_do_un_tac"]
            prev_end = row["thoi_gian_ket_thuc"]
            prev_update = row["thoi_gian_cap_nhat_cuoi"] or row["thoi_gian_bat_dau"]
            
            diff_sec = 999999.0
            prev_dt = _parse_datetime_safe(prev_update)
            if prev_dt:
                diff_sec = abs((cur_dt - prev_dt).total_seconds())

            # 1. Đợt ùn tắc trước đó CHƯA KẾT THÚC (thoi_gian_ket_thuc IS NULL):
            if prev_end is None:
                if prev_level == level:
                    # Cùng mức độ -> Cập nhật nhịp đập và ảnh, KHÔNG TẠO MỚI, KHÔNG PHÁT THÔNG BÁO LẶP
                    print(f"[Congestion DB Guard] Bỏ qua ghi đúp ùn tắc mức {level} (sự kiện #{prev_id} đang diễn ra)")
                    connection.execute(
                        "UPDATE nhat_ky_un_tac SET thoi_gian_cap_nhat_cuoi = ?, duong_dan_anh = COALESCE(?, duong_dan_anh) WHERE id = ?",
                        (start_time, duong_dan_anh, prev_id)
                    )
                    connection.commit()
                    return prev_id
                else:
                    # Mức độ thay đổi -> Kết thúc đợt cũ sạch sẽ
                    prev_start_dt = _parse_datetime_safe(row["thoi_gian_bat_dau"])
                    duration = max(0, int((cur_dt - prev_start_dt).total_seconds())) if prev_start_dt else 0
                    connection.execute(
                        "UPDATE nhat_ky_un_tac SET thoi_gian_ket_thuc = ?, thoi_gian_keo_dai_giay = ? WHERE id = ?",
                        (start_time, duration, prev_id)
                    )

            # 2. Đợt ùn tắc trước cùng mức độ vừa ghi cách đây <= 20 giây:
            elif prev_level == level and diff_sec <= 20.0:
                print(f"[Congestion DB Guard] Bỏ qua ghi đúp ùn tắc mức {level} vừa ghi cách đây {diff_sec:.1f}s (ID: {prev_id})")
                return prev_id

        # Chưa có sự kiện ùn tắc trùng -> Tạo mới
        cursor = connection.execute(
            """
            INSERT INTO nhat_ky_un_tac (id_camera, muc_do_un_tac, thoi_gian_bat_dau, duong_dan_anh, thoi_gian_cap_nhat_cuoi)
            VALUES (?, ?, ?, ?, ?)
            """,
            (camera_id, level, start_time, duong_dan_anh, start_time)
        )
        congestion_id = cursor.lastrowid
        
        # Thêm vào bảng thong_bao tập trung
        connection.execute(
            """
            INSERT INTO thong_bao (loai_thong_bao, id_ban_ghi, tieu_de, noi_dung, duong_dan_anh)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                'congestion', 
                congestion_id, 
                str(level),
                f"Cảnh báo ùn tắc mức {level} tại Camera {camera_id}",
                duong_dan_anh
            )
        )
        connection.commit()

        # Query và broadcast thời gian thực qua SSE
        cursor_notif = connection.execute(
            """
            SELECT id, loai_thong_bao as type, id_ban_ghi, tieu_de as title, noi_dung, duong_dan_anh as image, ngay_tao as time
            FROM thong_bao
            WHERE id = (SELECT last_insert_rowid())
            """
        )
        notif_row = dict(cursor_notif.fetchone())
        broadcast_notification(notif_row)

        return congestion_id


def update_congestion_end_time(congestion_id: int, end_time: str = None) -> None:
    """Cập nhật thời gian kết thúc cho sự kiện tắc nghẽn"""
    from datetime import datetime
    if end_time is None:
        end_time = datetime.now().isoformat()
    
    # Tính toán thời gian kéo dài (duration_seconds)
    with connect() as connection:
        row = connection.execute(
            "SELECT thoi_gian_bat_dau FROM nhat_ky_un_tac WHERE id = ?",
            (congestion_id,)
        ).fetchone()
        
        if row:
            start_dt = datetime.fromisoformat(row["thoi_gian_bat_dau"])
            end_dt = datetime.fromisoformat(end_time)
            duration_seconds = int((end_dt - start_dt).total_seconds())
            
            connection.execute(
                """
                UPDATE nhat_ky_un_tac 
                SET thoi_gian_ket_thuc = ?, thoi_gian_keo_dai_giay = ?
                WHERE id = ?
                """,
                (end_time, duration_seconds, congestion_id)
            )
            connection.commit()


def update_congestion_heartbeat(congestion_id: int) -> None:
    """Cập nhật nhịp đập heartbeat (thời gian cập nhật cuối) cho sự kiện ùn tắc"""
    from datetime import datetime
    now_str = datetime.now().isoformat()
    with connect() as connection:
        connection.execute(
            "UPDATE nhat_ky_un_tac SET thoi_gian_cap_nhat_cuoi = ? WHERE id = ?",
            (now_str, congestion_id)
        )
        connection.commit()


def update_violation_heartbeat(violation_id: int) -> None:
    """Cập nhật nhịp đập heartbeat (thời gian cập nhật cuối) cho vi phạm đỗ xe"""
    from datetime import datetime
    now_str = datetime.now().isoformat()
    with connect() as connection:
        connection.execute(
            "UPDATE vi_pham_do_xe SET thoi_gian_cap_nhat_cuoi = ? WHERE id = ?",
            (now_str, violation_id)
        )
        connection.commit()


def close_dangling_records_for_camera(camera_id: int) -> None:
    """Đóng tất cả các sự kiện ùn tắc và vi phạm đỗ xe chưa kết thúc của camera này khi camera bị dừng"""
    from datetime import datetime
    end_time = datetime.now().isoformat()
    
    with connect() as connection:
        # 1. Cập nhật ùn tắc giao thông (cần tính thời gian kéo dài dựa trên thoi_gian_cap_nhat_cuoi)
        rows = connection.execute(
            "SELECT id, thoi_gian_bat_dau, thoi_gian_cap_nhat_cuoi FROM nhat_ky_un_tac WHERE id_camera = ? AND thoi_gian_ket_thuc IS NULL",
            (camera_id,)
        ).fetchall()
        for row in rows:
            congestion_id = row["id"]
            start_time = row["thoi_gian_bat_dau"]
            cap_nhat_cuoi = row["thoi_gian_cap_nhat_cuoi"] or start_time
            try:
                start_dt = datetime.fromisoformat(start_time)
                end_dt = datetime.fromisoformat(cap_nhat_cuoi)
                duration = int((end_dt - start_dt).total_seconds())
            except Exception:
                duration = 0
            
            connection.execute(
                """
                UPDATE nhat_ky_un_tac 
                SET thoi_gian_ket_thuc = ?, thoi_gian_keo_dai_giay = ? 
                WHERE id = ?
                """,
                (cap_nhat_cuoi, duration, congestion_id)
            )
            
        # 2. Cập nhật vi phạm đỗ xe (cần set thoi_gian_ket_thuc = thoi_gian_cap_nhat_cuoi)
        rows = connection.execute(
            "SELECT id, thoi_gian_vi_pham, thoi_gian_cap_nhat_cuoi FROM vi_pham_do_xe WHERE id_camera = ? AND thoi_gian_ket_thuc IS NULL",
            (camera_id,)
        ).fetchall()
        for row in rows:
            violation_id = row["id"]
            start_time = row["thoi_gian_vi_pham"]
            cap_nhat_cuoi = row["thoi_gian_cap_nhat_cuoi"] or start_time
            try:
                start_dt = datetime.fromisoformat(start_time)
                end_dt = datetime.fromisoformat(cap_nhat_cuoi)
                duration_do = int((end_dt - start_dt).total_seconds())
            except Exception:
                duration_do = 0
                
            connection.execute(
                """
                UPDATE vi_pham_do_xe 
                SET thoi_gian_ket_thuc = ?, thoi_gian_do_giay = ?
                WHERE id = ?
                """,
                (cap_nhat_cuoi, duration_do, violation_id)
            )
        connection.commit()


def close_all_dangling_records() -> None:
    """Đóng tất cả các sự kiện ùn tắc và vi phạm đỗ xe chưa kết thúc trên toàn hệ thống (dùng khi startup)"""
    from datetime import datetime
    end_time = datetime.now().isoformat()
    
    with connect() as connection:
        # 1. Cập nhật ùn tắc giao thông (cần tính thời gian kéo dài dựa trên thoi_gian_cap_nhat_cuoi)
        rows = connection.execute(
            "SELECT id, thoi_gian_bat_dau, thoi_gian_cap_nhat_cuoi FROM nhat_ky_un_tac WHERE thoi_gian_ket_thuc IS NULL"
        ).fetchall()
        for row in rows:
            congestion_id = row["id"]
            start_time = row["thoi_gian_bat_dau"]
            cap_nhat_cuoi = row["thoi_gian_cap_nhat_cuoi"] or start_time
            try:
                start_dt = datetime.fromisoformat(start_time)
                end_dt = datetime.fromisoformat(cap_nhat_cuoi)
                duration = int((end_dt - start_dt).total_seconds())
            except Exception:
                duration = 0
            
            connection.execute(
                """
                UPDATE nhat_ky_un_tac 
                SET thoi_gian_ket_thuc = ?, thoi_gian_keo_dai_giay = ? 
                WHERE id = ?
                """,
                (cap_nhat_cuoi, duration, congestion_id)
            )
            
        # 2. Cập nhật vi phạm đỗ xe (cần set thoi_gian_ket_thuc = thoi_gian_cap_nhat_cuoi)
        rows = connection.execute(
            "SELECT id, thoi_gian_vi_pham, thoi_gian_cap_nhat_cuoi FROM vi_pham_do_xe WHERE thoi_gian_ket_thuc IS NULL"
        ).fetchall()
        for row in rows:
            violation_id = row["id"]
            start_time = row["thoi_gian_vi_pham"]
            cap_nhat_cuoi = row["thoi_gian_cap_nhat_cuoi"] or start_time
            try:
                start_dt = datetime.fromisoformat(start_time)
                end_dt = datetime.fromisoformat(cap_nhat_cuoi)
                duration_do = int((end_dt - start_dt).total_seconds())
            except Exception:
                duration_do = 0
                
            connection.execute(
                """
                UPDATE vi_pham_do_xe 
                SET thoi_gian_ket_thuc = ?, thoi_gian_do_giay = ?
                WHERE id = ?
                """,
                (cap_nhat_cuoi, duration_do, violation_id)
            )
        connection.commit()


def log_passed_vehicle(camera_id: int, bien_so_xe: str, loai_xe: str, thoi_gian_di_qua: str = None, duong_dan_anh: str = None) -> None:
    """Ghi lại lịch sử phương tiện đi qua có kèm ảnh với bảo vệ chống trùng lặp"""
    from datetime import datetime
    if thoi_gian_di_qua is None:
        thoi_gian_di_qua = datetime.now().isoformat()
    
    # Chuẩn hóa đường dẫn ảnh
    if duong_dan_anh:
        duong_dan_anh = duong_dan_anh.replace("\\", "/").replace("runtime/vehicles/", "logs/vehicles/")

    cur_dt = _parse_datetime_safe(thoi_gian_di_qua) or datetime.now()
        
    with connect() as connection:
        # BẢO VỆ TẦNG DATABASE: Chống ghi đúp cùng phương tiện trong vòng 5.0 giây
        cursor = connection.execute(
            """
            SELECT id, bien_so_xe, loai_xe, thoi_gian_di_qua
            FROM lich_su_phuong_tien
            WHERE id_camera = ?
            ORDER BY id DESC LIMIT 5
            """,
            (camera_id,)
        )
        recent_rows = cursor.fetchall()
        for row in recent_rows:
            prev_plate = row["bien_so_xe"]
            prev_type = row["loai_xe"]
            prev_time = row["thoi_gian_di_qua"]
            prev_dt = _parse_datetime_safe(prev_time)
            
            diff_sec = 999999.0
            if prev_dt:
                diff_sec = abs((cur_dt - prev_dt).total_seconds())

            if diff_sec <= 5.0:
                # 1. Trùng biển số trong vòng 5 giây -> Bỏ qua
                if bien_so_xe and prev_plate and bien_so_xe == prev_plate:
                    print(f"[Vehicle Pass DB Guard] Bỏ qua ghi đúp phương tiện '{bien_so_xe}' ({diff_sec:.1f}s)")
                    return
                # 2. Cùng loại xe và cùng ID tạm trong vòng 5 giây -> Bỏ qua
                if bien_so_xe and prev_plate and bien_so_xe == prev_plate and loai_xe == prev_type:
                    return

        connection.execute(
            """
            INSERT INTO lich_su_phuong_tien (id_camera, bien_so_xe, loai_xe, thoi_gian_di_qua, duong_dan_anh)
            VALUES (?, ?, ?, ?, ?)
            """,
            (camera_id, bien_so_xe, loai_xe, thoi_gian_di_qua, duong_dan_anh)
        )
        connection.commit()



def _levenshtein_distance(s1: str, s2: str) -> int:
    """Tính khoảng cách Levenshtein giữa 2 chuỗi để phát hiện biến thể biển số đọc sai 1 ký tự."""
    if len(s1) < len(s2):
        return _levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)
    prev = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        curr = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = prev[j + 1] + 1
            deletions = curr[j] + 1
            substitutions = prev[j] + (c1 != c2)
            curr.append(min(insertions, deletions, substitutions))
        prev = curr
    return prev[-1]


def log_detected_license_plate(license_plate: str, thoi_gian: str = None, ngay: str = None, detection_count: int = 1, avg_confidence: float = 0.0, image_paths: str = None, camera_id: int = 0) -> None:
    """Lưu biển số được phát hiện (Nhật ký từng lần) với bảo vệ chống ghi đúp tuyệt đối"""
    from datetime import datetime
    now = datetime.now()
    if ngay is None:
        detected_date = now.strftime("%Y-%m-%d")
    else:
        detected_date = ngay
        
    if thoi_gian is None:
        thoi_gian = now.strftime("%H:%M:%S")
    
    # Đảm bảo đường dẫn đúng cho web
    if image_paths:
        image_paths = image_paths.replace("\\", "/").replace("runtime/license_plates/", "logs/plates/")
    
    with connect() as connection:
        # BẢO VỆ TẦNG DATABASE: Chống ghi đúp biển số trên cùng camera trong khoảng thời gian ngắn (<= 8 giây)
        if license_plate and license_plate != "Không phát hiện biển số xe":
            cursor = connection.execute(
                """
                SELECT id, bien_so, thoi_gian_phat_hien FROM bien_so_phat_hien
                WHERE id_camera = ? AND ngay_phat_hien = ?
                ORDER BY id DESC LIMIT 5
                """,
                (camera_id, detected_date)
            )
            recent_rows = cursor.fetchall()
            for row in recent_rows:
                prev_plate = row["bien_so"]
                prev_time_str = row["thoi_gian_phat_hien"]
                try:
                    t1 = datetime.strptime(thoi_gian, "%H:%M:%S")
                    t2 = datetime.strptime(prev_time_str, "%H:%M:%S")
                    diff_sec = abs((t1 - t2).total_seconds())
                    if diff_sec <= 8.0:
                        # 1. Trùng 100% biển số trong vòng 8 giây -> Bỏ qua
                        if prev_plate == license_plate:
                            print(f"[ALPR DB Guard] Bỏ qua ghi đúp biển số '{license_plate}' (vừa ghi cách đây {diff_sec:.1f}s)")
                            return
                        # 2. Sai khác <= 1 ký tự (ví dụ 15C13863 vs 15C13063) trong vòng 8 giây -> Bỏ qua
                        if _levenshtein_distance(prev_plate, license_plate) <= 1:
                            print(f"[ALPR DB Guard] Bỏ qua ghi đúp biến thể OCR '{license_plate}' (tương đồng với '{prev_plate}', cách đây {diff_sec:.1f}s)")
                            return
                except Exception:
                    pass

        connection.execute(
            """
            INSERT INTO bien_so_phat_hien (bien_so, ngay_phat_hien, thoi_gian_phat_hien, so_lan_phat_hien, do_chinh_xac_tb, duong_dan_anh, id_camera)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (license_plate, detected_date, thoi_gian, detection_count, avg_confidence, image_paths, camera_id)
        )
        connection.commit()


def get_detected_license_plates(limit: int = 30, offset: int = 0, filter_type: str = None, search_query: str = None, camera_id: int = None, allowed_camera_ids: list = None) -> list:
    """Lấy danh sách biển số được phát hiện với bộ lọc, tìm kiếm và phân trang"""
    query = """
        SELECT b.id, b.bien_so as license_plate, b.ngay_phat_hien as detected_date, b.thoi_gian_phat_hien as detected_time, 
               b.so_lan_phat_hien as detection_count, b.do_chinh_xac_tb as avg_confidence, b.duong_dan_anh as image_paths,
               c.ten_camera as camera_name
        FROM bien_so_phat_hien b
        LEFT JOIN camera c ON b.id_camera = c.id
    """
    params = []
    conditions = []

    if allowed_camera_ids is not None:
        if not allowed_camera_ids:
            return []
        placeholders = ','.join('?' * len(allowed_camera_ids))
        conditions.append(f"b.id_camera IN ({placeholders})")
        params.extend(allowed_camera_ids)

    if filter_type == "has_plate":
        conditions.append("(b.bien_so != 'Không phát hiện biển số xe' AND b.bien_so != '' AND b.bien_so IS NOT NULL)")
    elif filter_type == "no_plate":
        conditions.append("(b.bien_so = 'Không phát hiện biển số xe' OR b.bien_so = '' OR b.bien_so IS NULL)")
    
    if search_query:
        conditions.append("b.bien_so LIKE ?")
        params.append(f"%{search_query}%")

    if camera_id is not None:
        conditions.append("b.id_camera = ?")
        params.append(camera_id)
        
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
        
    query += " ORDER BY b.ngay_phat_hien DESC, b.thoi_gian_phat_hien DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    
    with connect() as connection:
        rows = connection.execute(query, params).fetchall()
    return [dict(row) for row in rows]

def get_total_records_count(table_name: str, conditions_str: str = "", params: list = None) -> int:
    """Lấy tổng số bản ghi của một bảng theo điều kiện"""
    query = f"SELECT COUNT(*) as total FROM {table_name}"
    if conditions_str:
        query += f" WHERE {conditions_str}"
    
    with connect() as connection:
        row = connection.execute(query, params or []).fetchone()
    return row["total"] if row and row["total"] else 0


def get_license_plate_by_date(detected_date: str) -> list:
    """Lấy biển số được phát hiện trong ngày cụ thể"""
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT id, bien_so as license_plate, ngay_phat_hien as detected_date, thoi_gian_phat_hien as detected_time,
                   so_lan_phat_hien as detection_count, do_chinh_xac_tb as avg_confidence, duong_dan_anh as image_paths
            FROM bien_so_phat_hien
            WHERE ngay_phat_hien = ?
            ORDER BY thoi_gian_phat_hien DESC
            """,
            (detected_date,)
        ).fetchall()
    return [dict(row) for row in rows]


def get_dashboard_stats_data() -> dict:
    """Lấy tất cả dữ liệu thống kê cho dashboard"""
    return {
        "total_vehicles": get_total_vehicle_count(),
        "illegal_parking_violations": get_illegal_parking_violations(),
        "congestion_count": get_congestion_count(),
    }


def get_camera_settings(camera_id: int) -> dict:
    """Lấy cấu hình riêng biệt của một camera"""
    import json
    with connect() as connection:
        row = connection.execute(
            "SELECT confidence, frame_skip, congestion_threshold, parking_violation_time, log_retention, enabled_detect_labels FROM cai_dat_camera WHERE camera_id = ?",
            (camera_id,)
        ).fetchone()
        
        if row:
            raw_labels = row["enabled_detect_labels"]
            labels_list = None
            if raw_labels:
                try:
                    labels_list = json.loads(raw_labels)
                except Exception:
                    labels_list = None

            return {
                "camera_id": camera_id,
                "confidence": float(row["confidence"]),
                "frame_skip": int(row["frame_skip"]),
                "congestion_threshold": int(row["congestion_threshold"]),
                "parking_violation_time": int(row["parking_violation_time"]),
                "log_retention": row["log_retention"],
                "enabled_detect_labels": labels_list
            }
        
        # Fallback về mặc định
        return {
            "camera_id": camera_id,
            "confidence": 0.37,
            "frame_skip": 2,
            "congestion_threshold": 35,
            "parking_violation_time": 30,
            "log_retention": "30_days",
            "enabled_detect_labels": None
        }

def update_camera_settings(camera_id: int, settings: dict) -> None:
    """Cập nhật cấu hình riêng biệt của một camera"""
    import json
    raw_labels = settings.get("enabled_detect_labels")
    if raw_labels is not None and isinstance(raw_labels, list):
        labels_json = json.dumps(raw_labels)
    elif isinstance(raw_labels, str):
        labels_json = raw_labels
    else:
        labels_json = None

    with connect() as connection:
        # Nếu đã có cấu hình nhưng không truyền enabled_detect_labels thì giữ nguyên
        existing = connection.execute(
            "SELECT enabled_detect_labels FROM cai_dat_camera WHERE camera_id = ?",
            (camera_id,)
        ).fetchone()
        if labels_json is None and existing:
            labels_json = existing["enabled_detect_labels"]

        connection.execute(
            """
            INSERT OR REPLACE INTO cai_dat_camera (camera_id, confidence, frame_skip, congestion_threshold, parking_violation_time, log_retention, enabled_detect_labels)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                camera_id,
                float(settings.get("confidence", 0.37)),
                int(settings.get("frame_skip", 2)),
                int(settings.get("congestion_threshold", 35)),
                int(settings.get("parking_violation_time", 30)),
                settings.get("log_retention", "30_days"),
                labels_json
            )
        )
        connection.commit()


def get_system_setting(key: str, default: str = "") -> str:
    """Lấy cấu hình hệ thống từ bảng cai_dat_he_thong"""
    try:
        with connect() as connection:
            row = connection.execute(
                "SELECT gia_tri FROM cai_dat_he_thong WHERE khoa = ?",
                (key,)
            ).fetchone()
            if row:
                return row["gia_tri"]
    except Exception as e:
        print(f"[Database] Lỗi khi lấy cấu hình hệ thống {key}: {e}")
    return default


def update_system_setting(key: str, value: str) -> None:
    """Cập nhật hoặc thêm mới cấu hình hệ thống"""
    with connect() as connection:
        connection.execute(
            """
            INSERT OR REPLACE INTO cai_dat_he_thong (khoa, gia_tri)
            VALUES (?, ?)
            """,
            (key, str(value))
        )
        connection.commit()


def global_search(query: str) -> dict:
    """Tìm kiếm camera và biển số xe"""
    results = {
        "cameras": [],
        "plates": []
    }
    if not query:
        return results
        
    q = f"%{query}%"
    with connect() as connection:
        # 1. Tìm camera
        cam_rows = connection.execute(
            "SELECT id, ten_camera, mo_ta, trang_thai_hoat_dong FROM camera WHERE ten_camera LIKE ? OR mo_ta LIKE ? LIMIT 5",
            (q, q)
        ).fetchall()
        results["cameras"] = [dict(row) for row in cam_rows]
        
        # 2. Tìm biển số
        plate_rows = connection.execute(
            """
            SELECT bien_so as license_plate, ngay_phat_hien as detected_date, duong_dan_anh as image_paths 
            FROM bien_so_phat_hien 
            WHERE bien_so LIKE ? 
            ORDER BY ngay_cap_nhat DESC LIMIT 5
            """,
            (q,)
        ).fetchall()
        results["plates"] = [dict(row) for row in plate_rows]
        
    return results


def fix_image_paths() -> int:
    """Cập nhật đường dẫn ảnh cũ và chuẩn hóa dấu gạch chéo"""
    import os
    count = 0
    with connect() as connection:
        # 1. Chuẩn hóa dấu gạch ngược \ thành gạch xuôi /
        connection.execute("UPDATE vi_pham_do_xe SET duong_dan_anh = REPLACE(duong_dan_anh, '\\', '/')")
        connection.execute("UPDATE bien_so_phat_hien SET duong_dan_anh = REPLACE(duong_dan_anh, '\\', '/')")
        
        # 2. Đổi runtime thành logs nếu còn sót
        connection.execute("UPDATE vi_pham_do_xe SET duong_dan_anh = REPLACE(duong_dan_anh, 'runtime/violations/', 'logs/violations/')")
        connection.execute("UPDATE bien_so_phat_hien SET duong_dan_anh = REPLACE(duong_dan_anh, 'runtime/license_plates/', 'logs/plates/')")
        
        # 3. Xử lý trường hợp vi phạm chỉ lưu thư mục (ID_xxx/)
        # Chúng ta cần tìm file ảnh thực sự bên trong
        rows = connection.execute("SELECT id, duong_dan_anh FROM vi_pham_do_xe WHERE duong_dan_anh LIKE '%/'").fetchall()
        for row in rows:
            vid, path = row["id"], row["duong_dan_anh"]
            if not path: continue
            
            full_path = os.path.join(os.getcwd(), path.replace('/', os.sep))
            if os.path.isdir(full_path):
                # Tìm file .jpg bên trong (ưu tiên combined_alert.jpg)
                found_img = None
                for root, dirs, files in os.walk(full_path):
                    for f in files:
                        if f.endswith('.jpg'):
                            found_img = os.path.join(root, f).replace(os.getcwd(), '').replace(os.sep, '/').lstrip('/')
                            if 'combined_alert' in f:
                                break
                    if found_img: break
                
                if found_img:
                    connection.execute("UPDATE vi_pham_do_xe SET duong_dan_anh = ? WHERE id = ?", (found_img, vid))
                    count += 1
        
        connection.commit()
    return count


def migrate_camera_ids_and_plates() -> dict:
    """
    Migration function để:
    1. Cập nhật tất cả N/A hoặc NULL id_camera về 0
    2. Cập nhật đường dẫn biển số theo cấu trúc mới logs/plates/YYYY/MM/DD/
    """
    from datetime import datetime
    import re
    
    results = {
        "parking_violations_updated": 0,
        "congestion_records_updated": 0,
        "license_plates_updated": 0
    }
    
    with connect() as connection:
        # 1. Cập nhật id_camera từ N/A/NULL về 0 trong bảng vi_pham_do_xe
        cursor = connection.execute(
            "UPDATE vi_pham_do_xe SET id_camera = 0 WHERE id_camera IS NULL OR id_camera = 'N/A' OR CAST(id_camera AS TEXT) = 'N/A'"
        )
        connection.commit()
        results["parking_violations_updated"] = cursor.rowcount
        
        # 2. Cập nhật id_camera từ N/A/NULL về 0 trong bảng nhat_ky_un_tac
        cursor = connection.execute(
            "UPDATE nhat_ky_un_tac SET id_camera = 0 WHERE id_camera IS NULL OR id_camera = 'N/A' OR CAST(id_camera AS TEXT) = 'N/A'"
        )
        connection.commit()
        results["congestion_records_updated"] = cursor.rowcount
        
        # 3. Cập nhật đường dẫn biển số theo cấu trúc mới logs/plates/YYYY/MM/DD/
        rows = connection.execute(
            "SELECT id, bien_so, ngay_phat_hien, duong_dan_anh FROM bien_so_phat_hien WHERE duong_dan_anh IS NOT NULL"
        ).fetchall()
        
        for row in rows:
            plate_id = row["id"]
            plate_text = row["bien_so"]
            detected_date = row["ngay_phat_hien"]  # Format: YYYY-MM-DD
            old_path = row["duong_dan_anh"]
            
            # Skip nếu đã đúng format (chứa /YYYY/MM/DD/)
            if re.search(r'/\d{4}/\d{2}/\d{2}/', old_path):
                continue
            
            # Trích xuất năm, tháng, ngày từ detected_date
            try:
                date_obj = datetime.strptime(detected_date, "%Y-%m-%d")
                year = date_obj.strftime("%Y")
                month = date_obj.strftime("%m")
                day = date_obj.strftime("%d")
                
                # Trích xuất tên file từ đường dẫn cũ (phần sau dấu / cuối cùng)
                filename = old_path.split('/')[-1] if '/' in old_path else old_path
                
                # Tạo đường dẫn mới
                new_path = f"logs/plates/{year}/{month}/{day}/{filename}"
                
                # Cập nhật DB
                connection.execute(
                    "UPDATE bien_so_phat_hien SET duong_dan_anh = ? WHERE id = ?",
                    (new_path, plate_id)
                )
                results["license_plates_updated"] += 1
            except Exception as e:
                print(f"[Migration] Lỗi khi xử lý plate ID {plate_id}: {e}")
        
        connection.commit()
    
    return results


def get_user_camera_access(user_id: int) -> dict:
    """Lấy dict {id_camera: quyen} mà người dùng có quyền truy cập"""
    with connect() as connection:
        rows = connection.execute(
            "SELECT id_camera, quyen FROM camera_nguoi_dung WHERE id_nguoidung = ?",
            (user_id,)
        ).fetchall()
    return {row["id_camera"]: row["quyen"] for row in rows}

def get_user_camera_ids(user_id: int) -> list:
    """Hàm phụ trợ: Lấy danh sách ID camera (để tương thích ngược)"""
    return list(get_user_camera_access(user_id).keys())


def set_user_camera_access(user_id: int, camera_access: list) -> None:
    """Gán danh sách quyền truy cập camera cho người dùng.
    camera_access là list các dict: [{"id_camera": 1, "quyen": 1}, ...]
    """
    with connect() as connection:
        # Xóa toàn bộ quyền cũ
        connection.execute(
            "DELETE FROM camera_nguoi_dung WHERE id_nguoidung = ?",
            (user_id,)
        )
        # Thêm quyền mới
        for access in camera_access:
            cam_id = access.get("id_camera")
            quyen = access.get("quyen", 0)
            connection.execute(
                "INSERT OR IGNORE INTO camera_nguoi_dung (id_camera, id_nguoidung, quyen) VALUES (?, ?, ?)",
                (int(cam_id), user_id, int(quyen))
            )
        connection.commit()


def grant_camera_access(user_id: int, camera_id: int, quyen: int = 1) -> None:
    """Cấp quyền truy cập 1 camera cho người dùng"""
    with connect() as connection:
        connection.execute(
            "INSERT OR IGNORE INTO camera_nguoi_dung (id_camera, id_nguoidung, quyen) VALUES (?, ?, ?)",
            (camera_id, user_id, quyen)
        )
        connection.commit()


def revoke_camera_access(user_id: int, camera_id: int) -> None:
    """Thu hồi quyền truy cập 1 camera của người dùng"""
    with connect() as connection:
        connection.execute(
            "DELETE FROM camera_nguoi_dung WHERE id_nguoidung = ? AND id_camera = ?",
            (user_id, camera_id)
        )
        connection.commit()


def is_camera_enabled(camera_id: int) -> bool:
    """Kiểm tra xem camera có hoạt động hay không (trang_thai_hoat_dong = 1)"""
    try:
        with connect() as connection:
            row = connection.execute(
                "SELECT trang_thai_hoat_dong FROM camera WHERE id = ?",
                (camera_id,)
            ).fetchone()
            if row:
                return bool(row["trang_thai_hoat_dong"])
    except Exception as e:
        print(f"[Database] Lỗi kiểm tra trạng thái camera: {e}")
    return False

_local_dedup_cache = {}
_local_dedup_lock = threading.Lock()


def check_and_record_telegram_dedup(alert_key: str, cooldown_seconds: float = 60.0) -> bool:
    """
    Kiểm tra và ghi nhận chống gửi trùng thông báo Telegram xuyên tiến trình (Cross-Process Deduplication).
    Sử dụng atomic UPSERT trong SQLite kết hợp in-memory cache để loại bỏ hoàn toàn việc gửi trùng
    giữa nhiều tiến trình hoặc nhiều luồng cùng chạy.

    Trả về:
        True: Nếu thông báo bị trùng lặp trong khoảng cooldown_seconds (CẦN CHẶN / BỎ QUA).
        False: Nếu thông báo hợp lệ (được phép gửi) và đã ghi nhận mốc thời gian mới.
    """
    if not alert_key:
        return False

    clean_key = str(alert_key).strip()
    now = time.time()

    # 1. Fast-path: Kiểm tra cache trong cùng tiến trình
    with _local_dedup_lock:
        last_t = _local_dedup_cache.get(clean_key, 0.0)
        if now - last_t < cooldown_seconds:
            return True
        _local_dedup_cache[clean_key] = now

        # Dọn dẹp cache RAM nếu có hơn 1000 key
        if len(_local_dedup_cache) > 1000:
            stale = [k for k, v in _local_dedup_cache.items() if now - v > 3600.0]
            for k in stale:
                _local_dedup_cache.pop(k, None)

    # 2. Cross-process check: Atomic UPSERT vào SQLite
    try:
        DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(DATABASE_PATH, timeout=10.0) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS telegram_alert_dedup (
                    alert_key TEXT PRIMARY KEY,
                    sent_at REAL NOT NULL
                );
                """
            )
            cursor = conn.execute(
                """
                INSERT INTO telegram_alert_dedup (alert_key, sent_at)
                VALUES (?, ?)
                ON CONFLICT(alert_key) DO UPDATE SET sent_at = excluded.sent_at
                WHERE (excluded.sent_at - telegram_alert_dedup.sent_at) >= ?
                """,
                (clean_key, now, cooldown_seconds)
            )
            conn.commit()

            # Nếu rowcount == 0: đã có bản ghi và chưa hết cooldown -> Trùng lặp!
            if cursor.rowcount == 0:
                return True

            # Dọn dẹp định kỳ các bản ghi quá 2 giờ (xác suất 5%)
            if int(now) % 20 == 0:
                conn.execute(
                    "DELETE FROM telegram_alert_dedup WHERE sent_at < ?",
                    (now - 7200.0,)
                )
                conn.commit()
            return False
    except Exception as e:
        print(f"[Database] Lỗi kiểm tra telegram dedup: {e}")
        return False

