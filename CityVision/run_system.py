import uvicorn
import os
from dotenv import load_dotenv

# Load môi trường từ file .env
load_dotenv()

if __name__ == "__main__":
    print("="*50)
    print("Hệ thống Giám sát Giao thông Thông minh - UrbanM AI")
    print("Trạng thái: Đang khởi động Backend Web Server...")
    print("Địa chỉ: http://localhost:5000")
    print("="*50)

    # Reload=False để đảm bảo ổn định trong môi trường production/testing
    uvicorn.run("app:app", host="0.0.0.0", port=5000, reload=False)
