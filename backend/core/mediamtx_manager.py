import os
import sys
import platform
import subprocess
import urllib.request
import zipfile
import tarfile
import threading
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BIN_DIR = PROJECT_ROOT / "bin" / "mediamtx"
VERSION = "v1.9.0"

class MediaMTXManager:
    def __init__(self):
        self.process = None
        self.running = False
        self.thread = None
        self.os_name = platform.system().lower()
        self.machine = platform.machine().lower()
        self.bin_path = self._get_binary_path()

    def _get_binary_path(self) -> Path:
        if self.os_name == "windows":
            return BIN_DIR / "mediamtx.exe"
        else:
            return BIN_DIR / "mediamtx"

    def _get_download_url(self) -> str:
        base_url = f"https://github.com/bluenviron/mediamtx/releases/download/{VERSION}"
        if self.os_name == "windows":
            return f"{base_url}/mediamtx_{VERSION}_windows_amd64.zip"
        elif self.os_name == "linux":
            if "arm" in self.machine or "aarch64" in self.machine:
                return f"{base_url}/mediamtx_{VERSION}_linux_arm64v8.tar.gz"
            else:
                return f"{base_url}/mediamtx_{VERSION}_linux_amd64.tar.gz"
        else:
            raise RuntimeError(f"Hệ điều hành {self.os_name} không được hỗ trợ tự động.")

    def ensure_installed(self):
        """Đảm bảo MediaMTX đã được tải về và sẵn sàng chạy."""
        if self.bin_path.exists():
            return

        print(f"[MediaMTX] Không tìm thấy binary. Đang tiến hành tải phiên bản {VERSION} tự động...")
        BIN_DIR.mkdir(parents=True, exist_ok=True)
        url = self._get_download_url()
        archive_name = url.split("/")[-1]
        archive_path = BIN_DIR / archive_name

        # Tải file về
        try:
            print(f"[MediaMTX] Đang tải từ: {url}")
            urllib.request.urlretrieve(url, archive_path)
            print(f"[MediaMTX] Tải thành công: {archive_name}")
        except Exception as e:
            raise RuntimeError(f"Không thể tải MediaMTX từ internet: {e}. Vui lòng tự tải và đặt vào {BIN_DIR}")

        # Giải nén
        try:
            print(f"[MediaMTX] Đang giải nén...")
            if archive_name.endswith(".zip"):
                with zipfile.ZipFile(archive_path, 'r') as zip_ref:
                    zip_ref.extractall(BIN_DIR)
            else:
                with tarfile.open(archive_path, 'r:gz') as tar_ref:
                    tar_ref.extractall(BIN_DIR)
            
            # Cấp quyền chạy cho Linux/Jetson
            if self.os_name != "windows":
                os.chmod(self.bin_path, 0o755)

            # Dọn dẹp archive
            os.remove(archive_path)
            print(f"[MediaMTX] Giải nén thành công! Sẵn sàng tại {self.bin_path}")
        except Exception as e:
            raise RuntimeError(f"Lỗi giải nén MediaMTX: {e}")

    def _run(self):
        """Khởi động tiến trình chạy ngầm"""
        # Đọc cấu hình mặc định (nếu cần đổi cổng WebRTC/RTSP)
        # MediaMTX tự động tạo file mediamtx.yml nếu chưa có
        print(f"[MediaMTX] Khởi động tiến trình: {self.bin_path}")
        
        # Bỏ qua cổng trùng lặp nếu có tiến trình cũ
        startupinfo = None
        if self.os_name == "windows":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            
        self.process = subprocess.Popen(
            [str(self.bin_path)],
            cwd=str(BIN_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            startupinfo=startupinfo
        )

        # Đọc log
        for line in self.process.stdout:
            if not self.running:
                break
            print(f"[MediaMTX Log] {line.strip()}") # Bật khi cần debug

    def start(self):
        """Khởi chạy MediaMTX trong thread riêng biệt"""
        if self.running:
            return
        
        # Dọn dẹp các tiến trình mediamtx mồ côi cũ để tránh trùng cổng
        try:
            if self.os_name == "windows":
                subprocess.run(["taskkill", "/f", "/im", "mediamtx.exe"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                subprocess.run(["pkill", "-9", "mediamtx"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
        
        try:
            self.ensure_installed()
            yml_path = BIN_DIR / "mediamtx.yml"
            if yml_path.exists():
                with open(yml_path, "r", encoding="utf-8") as f:
                    content = f.read()
                
                modified = False
                
                # Kích hoạt WebRTC over TCP để bypass lỗi chặn local UDP/mDNS của Chrome/Edge trên Windows Dev
                if "webrtcLocalTCPAddress: ''" in content or 'webrtcLocalTCPAddress: ""' in content:
                    content = content.replace("webrtcLocalTCPAddress: ''", "webrtcLocalTCPAddress: :8189")
                    content = content.replace('webrtcLocalTCPAddress: ""', "webrtcLocalTCPAddress: :8189")
                    modified = True
                    print("[MediaMTX] Đã tự động kích hoạt WebRTC-over-TCP (:8189) để bypass bảo mật mDNS/Firewall.")
                
                # Cấu hình IP/Domain Public cho WebRTC (webrtcAdditionalHosts)
                public_host = os.getenv("WEBRTC_PUBLIC_HOST")
                if not public_host:
                    # Tự động lấy public IP ngoại mạng
                    try:
                        print("[MediaMTX] Đang tự động truy vấn địa chỉ IP Public...")
                        req = urllib.request.Request("https://api.ipify.org", headers={"User-Agent": "Mozilla/5.0"})
                        with urllib.request.urlopen(req, timeout=2.0) as response:
                            public_host = response.read().decode("utf-8").strip()
                        print(f"[MediaMTX] Đã phát hiện IP Public hiện tại: {public_host}")
                    except Exception as ex:
                        print(f"[MediaMTX] Không thể tự động lấy IP Public (Offline hoặc lỗi API): {ex}")
                
                import re
                if public_host and public_host.lower() != "disabled":
                    # Thay thế webrtcAdditionalHosts
                    new_hosts_line = f"webrtcAdditionalHosts: [{public_host}]"
                    content, count = re.subn(r"webrtcAdditionalHosts:\s*\[.*?\]", new_hosts_line, content)
                    if count > 0:
                        modified = True
                        print(f"[MediaMTX] Đã cấu hình host kết nối WebRTC: {public_host}")
                else:
                    # Nếu disabled hoặc không lấy được, reset về rỗng
                    content, count = re.subn(r"webrtcAdditionalHosts:\s*\[.*?\]", "webrtcAdditionalHosts: []", content)
                    if count > 0:
                        modified = True
                
                # Cấu hình TURN Server cho MediaMTX (webrtcICEServers2)
                turn_server = os.getenv("WEBRTC_TURN_SERVER")
                turn_user = os.getenv("WEBRTC_TURN_USERNAME", "")
                turn_pass = os.getenv("WEBRTC_TURN_PASSWORD", "")
                
                if turn_server and turn_server.lower() != "disabled":
                    ice_yaml = f"""webrtcICEServers2:
  - url: stun:stun.l.google.com:19302
  - url: {turn_server}
    username: '{turn_user}'
    password: '{turn_pass}'"""
                else:
                    ice_yaml = """webrtcICEServers2:
  - url: stun:stun.l.google.com:19302"""
                
                content, count = re.subn(r"webrtcICEServers2:\s*(?:\[\]|.*?(?=\n\S))", ice_yaml, content, flags=re.DOTALL)
                if count > 0:
                    modified = True
                    print(f"[MediaMTX] Đã cấu hình ICE/TURN Servers: {turn_server}")
                
                if modified:
                    with open(yml_path, "w", encoding="utf-8") as f:
                        f.write(content)
        except Exception as e:
            print(f"[MediaMTX] Lỗi cấu hình WebRTC: {e}")

        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        print("[MediaMTX] Dịch vụ đã được kích hoạt thành công chạy ngầm.")

    def stop(self):
        """Dừng tiến trình"""
        self.running = False
        if self.process:
            print("[MediaMTX] Đang tắt dịch vụ...")
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
            self.process = None
            print("[MediaMTX] Đã dừng dịch vụ.")

# Instance toàn cục để import và chạy
mediamtx = MediaMTXManager()
