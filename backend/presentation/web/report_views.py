from fastapi import APIRouter, Query, HTTPException
from fastapi.responses import StreamingResponse
import io
import os
import sqlite3
from typing import Optional
from datetime import datetime
from openpyxl import Workbook
from fpdf import FPDF
from backend.database import sqlite_db
import logging

router = APIRouter()
logger = logging.getLogger(__name__)

class PDFReport(FPDF):
    def __init__(self, title="Báo cáo"):
        super().__init__()
        self.report_title = title
        # Thêm font tiếng Việt
        font_path = os.path.join(os.getcwd(), "frontend", "static", "fonts", "Roboto-Regular.ttf")
        bold_font_path = os.path.join(os.getcwd(), "frontend", "static", "fonts", "Roboto-Bold.ttf")
        if os.path.exists(font_path):
            self.add_font("Roboto", "", font_path)
            self.add_font("Roboto", "B", bold_font_path)
            self.set_font("Roboto", "", 12)
        else:
            logger.warning(f"Không tìm thấy font tại {font_path}")
            self.set_font("Arial", "", 12)

    def header(self):
        self.set_font("Roboto", "B", 16)
        self.cell(0, 10, self.report_title, align="C", new_x="LMARGIN", new_y="NEXT")
        self.set_font("Roboto", "", 10)
        self.cell(0, 10, f"Ngày xuất: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", align="C", new_x="LMARGIN", new_y="NEXT")
        self.ln(10)

def fetch_report_data(report_type: str, start_date: str, end_date: str, camera_id: str):
    query = ""
    params = []
    
    if report_type == "traffic":
        query = """
            SELECT t.ngay_ghi_nhan as 'Ngày', c.ten_camera as 'Camera', t.so_luong_xe as 'Số lượng xe'
            FROM thong_ke_giao_thong t 
            LEFT JOIN camera c ON t.id_camera = c.id 
            WHERE 1=1
        """
        if start_date:
            query += " AND t.ngay_ghi_nhan >= ?"
            params.append(start_date)
        if end_date:
            query += " AND t.ngay_ghi_nhan <= ?"
            params.append(end_date)
        if camera_id and camera_id != "all":
            query += " AND t.id_camera = ?"
            params.append(int(camera_id))
        query += " ORDER BY t.ngay_ghi_nhan DESC"
            
    elif report_type == "parking":
        query = """
            SELECT p.thoi_gian_vi_pham as 'Thời gian', c.ten_camera as 'Camera', 
                   IFNULL(p.bien_so, 'Không xác định') as 'Biển số', 
                   p.thoi_gian_do_giay as 'Thời gian đỗ (giây)', 
                   CASE WHEN p.da_giai_quyet = 1 THEN 'Đã xử lý' ELSE 'Chưa xử lý' END as 'Trạng thái'
            FROM vi_pham_do_xe p 
            LEFT JOIN camera c ON p.id_camera = c.id 
            WHERE 1=1
        """
        if start_date:
            query += " AND DATE(p.thoi_gian_vi_pham) >= ?"
            params.append(start_date)
        if end_date:
            query += " AND DATE(p.thoi_gian_vi_pham) <= ?"
            params.append(end_date)
        if camera_id and camera_id != "all":
            query += " AND p.id_camera = ?"
            params.append(int(camera_id))
        query += " ORDER BY p.thoi_gian_vi_pham DESC"

    elif report_type == "congestion":
        query = """
            SELECT n.thoi_gian_bat_dau as 'Bắt đầu', IFNULL(n.thoi_gian_ket_thuc, 'Đang diễn ra') as 'Kết thúc', 
                   c.ten_camera as 'Camera', n.muc_do_un_tac as 'Mức độ', 
                   IFNULL(n.thoi_gian_keo_dai_giay, 0) as 'Kéo dài (giây)'
            FROM nhat_ky_un_tac n 
            LEFT JOIN camera c ON n.id_camera = c.id 
            WHERE 1=1
        """
        if start_date:
            query += " AND DATE(n.thoi_gian_bat_dau) >= ?"
            params.append(start_date)
        if end_date:
            query += " AND DATE(n.thoi_gian_bat_dau) <= ?"
            params.append(end_date)
        if camera_id and camera_id != "all":
            query += " AND n.id_camera = ?"
            params.append(int(camera_id))
        query += " ORDER BY n.thoi_gian_bat_dau DESC"
    else:
        raise ValueError("Invalid report type")

    with sqlite_db.connect() as conn:
        cursor = conn.execute(query, params)
        columns = [description[0] for description in cursor.description]
        rows = cursor.fetchall()
        
    return columns, [dict(row) for row in rows]

def create_excel(columns, data, report_title):
    wb = Workbook()
    ws = wb.active
    ws.title = "Báo cáo"
    
    # Tiêu đề cột
    ws.append(columns)
    
    # Dữ liệu
    for row in data:
        ws.append([row[col] for col in columns])
        
    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output

def create_pdf(columns, data, report_title):
    pdf = PDFReport(title=report_title)
    pdf.add_page()
    
    # Bảng dữ liệu
    pdf.set_font("Roboto", "B", 10)
    col_widths = [190 / len(columns)] * len(columns)
    
    # Vẽ tiêu đề cột (Header row)
    for i, col in enumerate(columns):
        pdf.cell(col_widths[i], 10, str(col), border=1, align="C")
    pdf.ln()
    
    # Vẽ các dòng dữ liệu (Data rows)
    pdf.set_font("Roboto", "", 10)
    for row in data:
        for i, col in enumerate(columns):
            val = str(row[col])
            pdf.cell(col_widths[i], 10, val[:30], border=1, align="C") # Cắt ngắn nếu quá dài
        pdf.ln()
        
    output = io.BytesIO(pdf.output(dest="S"))
    output.seek(0)
    return output

@router.get("/export")
async def export_report(
    report_type: str = Query(..., description="traffic, parking, congestion"),
    format: str = Query(..., description="excel or pdf"),
    start_date: str = Query(""),
    end_date: str = Query(""),
    camera_id: str = Query("all")
):
    titles = {
        "traffic": "BÁO CÁO LƯU LƯỢNG GIAO THÔNG",
        "parking": "BÁO CÁO VI PHẠM DỪNG ĐỖ XE TRÁI QUY ĐỊNH",
        "congestion": "BÁO CÁO TÌNH HÌNH ÙN TẮC GIAO THÔNG"
    }
    
    if report_type not in titles:
        raise HTTPException(status_code=400, detail="Loại báo cáo không hợp lệ")
        
    try:
        columns, data = fetch_report_data(report_type, start_date, end_date, camera_id)
        report_title = titles[report_type]
        
        filename_prefix = f"report_{report_type}_{datetime.now().strftime('%Y%m%d%H%M')}"
        
        if format == "excel":
            file_stream = create_excel(columns, data, report_title)
            return StreamingResponse(
                file_stream, 
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                headers={"Content-Disposition": f"attachment; filename={filename_prefix}.xlsx"}
            )
        elif format == "pdf":
            file_stream = create_pdf(columns, data, report_title)
            return StreamingResponse(
                file_stream, 
                media_type="application/pdf",
                headers={"Content-Disposition": f"attachment; filename={filename_prefix}.pdf"}
            )
        else:
            raise HTTPException(status_code=400, detail="Định dạng không hợp lệ")
            
    except Exception as e:
        logger.error(f"Lỗi xuất báo cáo: {e}")
        raise HTTPException(status_code=500, detail=f"Lỗi hệ thống khi xuất báo cáo: {str(e)}")
