import sqlite3
import os
from pathlib import Path
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash

from core.config import DATABASE_PATH


def connect() -> sqlite3.Connection:
    """Tạo kết nối mới tới CSDL SQLite"""
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH)
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
                ngay_tao TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS cau_hinh_he_thong (
                khoa TEXT PRIMARY KEY,
                gia_tri TEXT
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
            """
        )

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


        # Cấu hình mặc định
        default_settings = {
            "confidence": "0.32",
            "frame_skip": "1",
            "iou_threshold": "0.45",
            "congestion_threshold": "70",
            "parking_violation_time": "30",
            "log_retention": "30_days",
            "evidence_format": "jpg"
        }
        for k, v in default_settings.items():
            connection.execute(
                "INSERT OR IGNORE INTO cau_hinh_he_thong (khoa, gia_tri) VALUES (?, ?)",
                (k, v)
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
        connection.commit()





def get_illegal_parking_violations(limit: int = 30, offset: int = 0, filter_type: str = None, date: str = None, hour: str = None, camera_id: int = None, record_id: int = None) -> list:
    """Lấy danh sách xe đỗ sai có phân trang và bộ lọc"""
    query = """
        SELECT pv.id, pv.id_camera as camera_id, pv.bien_so as license_plate, pv.thoi_gian_vi_pham as violation_time,
               pv.thoi_gian_do_giay as duration_seconds, pv.duong_dan_anh as frame_path, pv.da_giai_quyet as is_resolved,
               pv.ngay_tao as created_at, c.ten_camera as camera_name
        FROM vi_pham_do_xe pv
        LEFT JOIN camera c ON pv.id_camera = c.id
    """
    conditions = []
    params = []

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

def get_illegal_parking_count(start_date: str = None, end_date: str = None) -> int:
    """Lấy tổng số vi phạm đỗ xe trong khoảng thời gian"""
    query = "SELECT COUNT(*) as total FROM vi_pham_do_xe"
    params = []
    
    if start_date or end_date:
        conditions = []
        if start_date:
            conditions.append("DATE(thoi_gian_vi_pham) >= ?")
            params.append(start_date)
        if end_date:
            conditions.append("DATE(thoi_gian_vi_pham) <= ?")
            params.append(end_date)
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


def get_congestion_count(start_date: str = None, end_date: str = None) -> int:
    """Lấy tổng số lần tắc nghẽn trong khoảng thời gian"""
    query = "SELECT COUNT(*) as total FROM nhat_ky_un_tac"
    params = []
    
    if start_date or end_date:
        conditions = []
        if start_date:
            conditions.append("DATE(thoi_gian_bat_dau) >= ?")
            params.append(start_date)
        if end_date:
            conditions.append("DATE(thoi_gian_bat_dau) <= ?")
            params.append(end_date)
        query += " WHERE " + " AND ".join(conditions)
        
    with connect() as connection:
        row = connection.execute(query, params).fetchone()
    return row["total"] if row and row["total"] else 0

def get_congestion_history(limit: int = 30, offset: int = 0, level: int = None, date: str = None, hour: str = None, camera_id: int = None, record_id: int = None) -> list:    
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

def get_daily_vehicle_stats(start_date: str = None, end_date: str = None, limit: int = 30) -> list:
    """Lấy thống kê lưu lượng xe hàng ngày, có hỗ trợ lọc theo khoảng thời gian"""
    query = "SELECT ngay_ghi_nhan as date, SUM(so_luong_xe) as count FROM thong_ke_giao_thong"
    params = []
    
    if start_date or end_date:
        conditions = []
        if start_date:
            conditions.append("ngay_ghi_nhan >= ?")
            params.append(start_date)
        if end_date:
            conditions.append("ngay_ghi_nhan <= ?")
            params.append(end_date)
        query += " WHERE " + " AND ".join(conditions)
    
    query += " GROUP BY ngay_ghi_nhan ORDER BY ngay_ghi_nhan DESC LIMIT ?"
    params.append(limit)
    
    with connect() as connection:
        rows = connection.execute(query, params).fetchall()
    return [dict(row) for row in reversed(rows)]

def get_latest_violations(limit: int = 5) -> list:
    """Lấy danh sách vi phạm mới nhất"""
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT pv.id, pv.bien_so as license_plate, pv.thoi_gian_vi_pham as time, 
                   c.ten_camera as camera_name
            FROM vi_pham_do_xe pv
            LEFT JOIN camera c ON pv.id_camera = c.id
            ORDER BY pv.thoi_gian_vi_pham DESC
            LIMIT ?
            """,
            (limit,)
        ).fetchall()
    return [dict(row) for row in rows]

# Danh sách queues lắng nghe thông báo thời gian thực phục vụ SSE
active_sse_queues = []

def broadcast_notification(notification_data: dict):
    """Gửi thông báo mới tức thời tới tất cả các kết nối SSE đang hoạt động, tương thích đa luồng"""
    import asyncio
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


def get_total_vehicle_count(start_date: str = None, end_date: str = None) -> int:
    """Lấy tổng số xe đi qua trực tiếp từ bảng lịch sử (loại bỏ person và license_plate)"""
    query = "SELECT COUNT(*) as total FROM lich_su_phuong_tien WHERE loai_xe NOT IN ('person', 'license_plate')"
    params = []
    
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

def get_vehicle_type_distribution(start_date: str = None, end_date: str = None) -> list:
    """Lấy tỷ lệ các loại phương tiện trong khoảng thời gian xác định (loại bỏ person và license_plate)"""
    query = "SELECT loai_xe as type, COUNT(*) as count FROM lich_su_phuong_tien WHERE loai_xe NOT IN ('person', 'license_plate')"
    params = []
    
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


def log_vehicle_count(camera_id: int, vehicle_type: str, count: int = 1) -> None:
    """Ghi nhận số lượng xe theo loại và ngày (loại bỏ người và biển số)"""
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


def log_parking_violation(camera_id: int, license_plate: str = None, violation_time: str = None, duration: int = 0, frame_path: str = None) -> None:
    """Ghi lại vi phạm đỗ xe"""
    from datetime import datetime
    if violation_time is None:
        violation_time = datetime.now().isoformat()
    
    # Đảm bảo đường dẫn lưu vào DB là tương đối và đúng thư mục logs/violations/
    if frame_path and "runtime/violations/" in frame_path:
        frame_path = frame_path.replace("runtime/violations/", "logs/violations/")
    
    with connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO vi_pham_do_xe (id_camera, bien_so, thoi_gian_vi_pham, thoi_gian_do_giay, duong_dan_anh)
            VALUES (?, ?, ?, ?, ?)
            """,
            (camera_id, license_plate, violation_time, duration, frame_path)
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


def log_congestion(camera_id: int, level: int = 1, start_time: str = None, duong_dan_anh: str = None) -> int:
    """Ghi lại sự kiện tắc nghẽn và trả về ID của record"""
    from datetime import datetime
    if start_time is None:
        start_time = datetime.now().isoformat()
    
    with connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO nhat_ky_un_tac (id_camera, muc_do_un_tac, thoi_gian_bat_dau, duong_dan_anh)
            VALUES (?, ?, ?, ?)
            """,
            (camera_id, level, start_time, duong_dan_anh)
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


def log_passed_vehicle(camera_id: int, bien_so_xe: str, loai_xe: str, thoi_gian_di_qua: str = None, duong_dan_anh: str = None) -> None:
    """Ghi lại lịch sử phương tiện đi qua có kèm ảnh"""
    from datetime import datetime
    if thoi_gian_di_qua is None:
        thoi_gian_di_qua = datetime.now().isoformat()
    
    # Chuẩn hóa đường dẫn ảnh
    if duong_dan_anh:
        duong_dan_anh = duong_dan_anh.replace("\\", "/").replace("runtime/vehicles/", "logs/vehicles/")
        
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO lich_su_phuong_tien (id_camera, bien_so_xe, loai_xe, thoi_gian_di_qua, duong_dan_anh)
            VALUES (?, ?, ?, ?, ?)
            """,
            (camera_id, bien_so_xe, loai_xe, thoi_gian_di_qua, duong_dan_anh)
        )
        connection.commit()


def log_detected_license_plate(license_plate: str, thoi_gian: str = None, ngay: str = None, detection_count: int = 1, avg_confidence: float = 0.0, image_paths: str = None, camera_id: int = 0) -> None:
    """Lưu biển số được phát hiện (Nhật ký từng lần)"""
    from datetime import datetime
    now = datetime.now()
    if ngay is None:
        detected_date = now.strftime("%Y-%m-%d")
    else:
        detected_date = ngay
        
    if thoi_gian is None:
        thoi_gian = now.strftime("%H:%M:%S")
    
    # Đàm bảo đường dẫn đúng cho web
    if image_paths:
        image_paths = image_paths.replace("\\", "/").replace("runtime/license_plates/", "logs/plates/")
    
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO bien_so_phat_hien (bien_so, ngay_phat_hien, thoi_gian_phat_hien, so_lan_phat_hien, do_chinh_xac_tb, duong_dan_anh, id_camera)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (license_plate, detected_date, thoi_gian, detection_count, avg_confidence, image_paths, camera_id)
        )
        connection.commit()


def get_detected_license_plates(limit: int = 30, offset: int = 0, filter_type: str = None, search_query: str = None, camera_id: int = None) -> list:
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


def get_system_settings() -> dict:
    """Lấy tất cả cấu hình hệ thống"""
    with connect() as connection:
        rows = connection.execute("SELECT khoa, gia_tri FROM cau_hinh_he_thong").fetchall()
        # Chuyển đổi sang dict với kiểu dữ liệu phù hợp
        raw = {row["khoa"]: row["gia_tri"] for row in rows}
        
        # Parse giá trị số
        processed = {}
        for k, v in raw.items():
            if k in ["confidence", "iou_threshold"]:
                processed[k] = float(v)
            elif k in ["frame_skip", "congestion_threshold", "parking_violation_time"]:
                processed[k] = int(v)
            else:
                processed[k] = v
        return processed

def update_system_settings(settings: dict) -> None:
    """Cập nhật các cấu hình hệ thống"""
    with connect() as connection:
        for k, v in settings.items():
            connection.execute(
                "UPDATE cau_hinh_he_thong SET gia_tri = ? WHERE khoa = ?",
                (str(v), k)
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
