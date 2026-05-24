import subprocess
import os
import hmac
import hashlib
from fastapi import APIRouter, BackgroundTasks, Request, HTTPException

webhook_router = APIRouter()

WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET", "")

def verify_signature(request: Request, payload_body: bytes) -> bool:
    if not WEBHOOK_SECRET:
        return True # Nếu không cấu hình secret, cho qua
    
    signature_header = request.headers.get("x-hub-signature-256")
    if not signature_header:
        return False
        
    hash_object = hmac.new(WEBHOOK_SECRET.encode("utf-8"), msg=payload_body, digestmod=hashlib.sha256)
    expected_signature = "sha256=" + hash_object.hexdigest()
    return hmac.compare_digest(expected_signature, signature_header)

def run_update_script():
    # Tìm đường dẫn tuyệt đối của script update_code.sh ở thư mục gốc dự án
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    script_path = os.path.join(base_dir, "update_code.sh")
    
    if os.path.exists(script_path):
        # Chạy script dưới nền để không cản trở API phản hồi,subprocess.Popen giúp tách biệt tiến trình
        subprocess.Popen(["bash", script_path], cwd=base_dir)

@webhook_router.post("/api/webhook/deploy", tags=["Deploy"])
async def github_webhook(request: Request, background_tasks: BackgroundTasks):
    payload_body = await request.body()
    
    # Xác thực nguồn gửi (Github)
    if not verify_signature(request, payload_body):
        raise HTTPException(status_code=403, detail="Từ chối truy cập: Chữ ký Webhook không hợp lệ")
    
    # Phân loại sự kiện (chỉ nhận Push)
    event = request.headers.get("x-github-event")
    if event == "push":
        background_tasks.add_task(run_update_script)
        return {"status": "success", "message": "Đã nhận Push! Hệ thống Jetson đang tự động kéo Code và khởi động lại..."}
    
    return {"status": "ignored", "message": f"Bỏ qua sự kiện: {event}"}
