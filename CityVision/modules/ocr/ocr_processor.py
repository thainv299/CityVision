import cv2
import numpy as np
import re

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
    # Tránh chia cho 0 hoặc lỗi nếu crop quá nhỏ
    if h == 0 or w == 0:
        return img_bgr, "Error", w, h

    ratio = w / h
    if ratio < 1.8:
        dst_w, dst_h = 240, 180   
    else:
        dst_w, dst_h = 480, 120   

    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    edged = cv2.Canny(blur, 50, 200)
    dilated = cv2.dilate(edged, np.ones((3, 3), np.uint8), iterations=1)
    contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contours = sorted(contours, key=cv2.contourArea, reverse=True)[:5]

    rect_pts = None
    img_area = h * w  # Tính tổng diện tích bức ảnh crop

    for c in contours:
        perimeter = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, 0.03 * perimeter, True)
        
        # Chỉ nắn góc nếu tìm thấy 4 điểm VÀ diện tích khung đó phải > 40% diện tích ảnh
        if len(approx) == 4:
            contour_area = cv2.contourArea(approx)
            if contour_area > (img_area * 0.4): 
                rect_pts = approx.reshape(4, 2)
                break

    if rect_pts is not None:
        ordered_pts = order_points(rect_pts)
        dst_pts = np.array([[0, 0], [dst_w - 1, 0], [dst_w - 1, dst_h - 1], [0, dst_h - 1]], dtype="float32")
        M = cv2.getPerspectiveTransform(ordered_pts, dst_pts)
        img_plate_color = cv2.warpPerspective(img_bgr, M, (dst_w, dst_h))
        status_text = f"Nan goc ({dst_w}x{dst_h})"
    else:
        img_plate_color = cv2.resize(img_bgr, (dst_w, dst_h), interpolation=cv2.INTER_CUBIC)
        status_text = f"Phong to ({dst_w}x{dst_h})"
    
    return img_plate_color, status_text, dst_w, dst_h

def preprocess_plate(img_bgr):
    h, w = img_bgr.shape[:2]
    img_scaled = cv2.resize(img_bgr, (w * 2, h * 2), interpolation=cv2.INTER_CUBIC)
    
    img_gray = cv2.cvtColor(img_scaled, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=1.5, tileGridSize=(4, 4))
    img_enhanced = clahe.apply(img_gray)
    
    img_enhanced_bgr = cv2.cvtColor(img_enhanced, cv2.COLOR_GRAY2BGR)
    return img_enhanced_bgr

def correct_plate_format(text):
    """Sửa lỗi OCR: Ép đúng vị trí nào là số, vị trí nào là chữ."""
    if len(text) < 6:
        return text

    dict_char_to_num = {'O': '0', 'Q': '0', 'I': '1', 'Z': '2', 'S': '5', 'G': '6', 'B': '8', 'A': '4'}
    dict_num_to_char = {'0': 'D', '8': 'B', '4': 'A', '5': 'S', '2': 'Z'}

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
    # Tiền xử lý (CLAHE)
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
