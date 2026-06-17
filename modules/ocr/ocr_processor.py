import cv2
import numpy as np
import re
from shapely.geometry import Polygon

def order_points(pts):
    rect = np.zeros((4, 2), dtype="float32")
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]
    rect[2] = pts[np.argmax(s)]
    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)]
    rect[3] = pts[np.argmax(diff)]
    return rect

def get_plate_perspective(img_bgr):
    h, w = img_bgr.shape[:2]
    if h == 0 or w == 0:
        return img_bgr, "Error", w, h

    ratio = w / h
    if ratio < 1.8:
        dst_w, dst_h = 240, 180   
    else:
        dst_w, dst_h = 480, 120   

    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    
    # 1. Dùng Canny để tìm viền
    edged = cv2.Canny(blur, 50, 150)
    
    # 2. Dùng phép đóng (Morphology Close) để nối các nét đứt ở viền biển số thành 1 khung khép kín
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    closed = cv2.morphologyEx(edged, cv2.MORPH_CLOSE, kernel)
    
    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contours = sorted(contours, key=cv2.contourArea, reverse=True)[:5]

    rect_pts = None
    img_area = h * w

    for c in contours:
        contour_area = cv2.contourArea(c)
        # Chỉ xét các contour có diện tích chiếm trên 35% ảnh crop
        if contour_area > (img_area * 0.35): 
            # Dùng minAreaRect để tính khung chữ nhật xoay (ổn định hơn rất nhiều approxPolyDP)
            rect = cv2.minAreaRect(c)
            box = cv2.boxPoints(rect)
            rect_pts = np.array(box, dtype="float32")
            break

    if rect_pts is not None:
        ordered_pts = order_points(rect_pts)
        dst_pts = np.array([[0, 0], [dst_w - 1, 0], [dst_w - 1, dst_h - 1], [0, dst_h - 1]], dtype="float32")
        M = cv2.getPerspectiveTransform(ordered_pts, dst_pts)
        img_plate_color = cv2.warpPerspective(img_bgr, M, (dst_w, dst_h))
        status_text = f"MinAreaRect ({dst_w}x{dst_h})"
    else:
        img_plate_color = cv2.resize(img_bgr, (dst_w, dst_h), interpolation=cv2.INTER_CUBIC)
        status_text = f"Phong to ({dst_w}x{dst_h})"
    
    return img_plate_color, status_text, dst_w, dst_h


def preprocess_plate(img_bgr):
    h, w = img_bgr.shape[:2]
    img_scaled = cv2.resize(img_bgr, (w * 2, h * 2), interpolation=cv2.INTER_CUBIC)
    
    img_gray = cv2.cvtColor(img_scaled, cv2.COLOR_BGR2GRAY)
    img_gray_bgr = cv2.cvtColor(img_gray, cv2.COLOR_GRAY2BGR)
    return img_gray_bgr

def correct_plate_format(text):
    """Sửa lỗi OCR: Ép đúng vị trí nào là số, vị trí nào là chữ."""
    if len(text) < 6:
        return text

    dict_char_to_num = {'O': '0', 'Q': '0', 'I': '1', 'Z': '2', 'S': '5', 'G': '6', 'B': '8', 'A': '4'}
    dict_num_to_char = {'0': 'D', '8': 'B', '4': 'A', '5': 'S', '2': 'Z', '6': 'G'}

    text_list = list(text)

    # Hai ký tự đầu tiên (Mã tỉnh) -> Nếu bị nhầm thành chữ thì sửa thành số
    for i in range(0, min(2, len(text_list))):
        if text_list[i].isalpha() and text_list[i] in dict_char_to_num:
            text_list[i] = dict_char_to_num[text_list[i]]

    # Ký tự thứ 3 (Sê-ri) -> Đổi số thành chữ (A, B, C...)
    if len(text_list) > 2 and text_list[2].isdigit() and text_list[2] in dict_num_to_char:
        text_list[2] = dict_num_to_char[text_list[2]]

    # Kể từ ký tự thứ 4 trở đi -> Ép thành số
    for i in range(3, len(text_list)):
        if text_list[i].isalpha() and text_list[i] in dict_char_to_num:
            text_list[i] = dict_char_to_num[text_list[i]]

    return "".join(text_list)

def is_valid_vn_plate(text):
    pattern = r"^[1-9][0-9][A-Z][0-9]{4,5}$"
    return bool(re.match(pattern, text))

def run_ocr(ocr_reader, img_bgr):
    # Lấy ảnh đã căn góc/bóp méo
    img_plate_color, status_text, dst_w, dst_h = get_plate_perspective(img_bgr)
    # Tiền xử lý (Phóng to, Xám)
    img_processed = preprocess_plate(img_plate_color)
    read_text = ""
    
    res = ocr_reader.ocr(img_processed, cls=False)
    
    if res and res[0]:
        lines = sorted(res[0], key=lambda x: x[0][0][1])
        for line in lines:
            read_text += line[1][0].upper()

    clean_text = re.sub(r'[^A-Z0-9]', '', read_text)
    final_text = correct_plate_format(clean_text)
    
    return clean_text, final_text, img_processed, img_plate_color, status_text, dst_w, dst_h