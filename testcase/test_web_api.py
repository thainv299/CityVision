import unittest
import sys
from pathlib import Path

# Setup paths
PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from fastapi.testclient import TestClient

class TestWebAPIEndpoints(unittest.TestCase):
    """Kiểm tra phản hồi của các REST API endpoint và Giao diện Web"""

    @classmethod
    def setUpClass(cls):
        from app import app
        cls.client = TestClient(app)

    def test_01_health_and_home_route(self):
        """Kiểm tra route trang chủ / login"""
        response = self.client.get("/login")
        self.assertIn(response.status_code, [200, 302, 307])
        print(f"-> [OK] GET /login status code: {response.status_code}")

    def test_02_system_settings_api(self):
        """Kiểm tra GET & PUT /api/system/settings"""
        # GET
        get_res = self.client.get("/api/system/settings")
        self.assertIn(get_res.status_code, [200, 401])
        if get_res.status_code == 200:
            data = get_res.json()
            self.assertTrue(data.get("ok", False))

        # PUT
        payload = {
            "log_retention": "30_days",
            "telegram_notifications": "true",
            "wan_transmission_mode": "mjpeg"
        }
        put_res = self.client.put("/api/system/settings", json=payload)
        self.assertIn(put_res.status_code, [200, 401])
        print("-> [OK] GET & PUT /api/system/settings test passed")

    def test_03_camera_api(self):
        """Kiểm tra API lấy danh sách Camera /api/cameras"""
        res = self.client.get("/api/cameras")
        self.assertIn(res.status_code, [200, 401, 403])  # 200 if public/mock, 401 if auth required
        print(f"-> [OK] GET /api/cameras response code: {res.status_code}")

    def test_04_weapons_api(self):
        """Kiểm tra API phát hiện vũ khí /api/weapons"""
        res = self.client.get("/api/weapons?limit=5")
        self.assertIn(res.status_code, [200, 401])
        if res.status_code == 200:
            data = res.json()
            self.assertIn("weapons", data)
            print(f"-> [OK] GET /api/weapons returned {len(data.get('weapons', []))} records")
        else:
            print(f"-> [OK] GET /api/weapons auth code: {res.status_code}")

    def test_05_violations_api(self):
        """Kiểm tra API vi phạm đỗ xe /api/violations"""
        res = self.client.get("/api/violations?limit=5")
        self.assertIn(res.status_code, [200, 401])
        print(f"-> [OK] GET /api/violations status: {res.status_code}")

if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    unittest.main()
