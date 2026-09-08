import unittest
import sys
import time
import os
from pathlib import Path

# Fix UTF-8 output encoding for Windows terminal
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Setup project path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

# Import individual test modules
from testcase.test_imports import TestModuleImports
from testcase.test_database import TestDatabaseAndRepositories
from testcase.test_managers import TestBusinessManagers
from testcase.test_web_api import TestWebAPIEndpoints

def run_suite():
    print("=" * 75)
    print(" 🚀 BỘ KIỂM THỬ TỰ ĐỘNG HỆ THỐNG CITYVISION AI (AUTOMATED TEST SUITE)")
    print("=" * 75)
    print(f" 📍 Thư mục gốc: {PROJECT_ROOT}")
    print(f" ⏰ Thời gian khởi chạy: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 75 + "\n")

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # Thêm các bộ test case theo thứ tự hợp lý
    suite.addTests(loader.loadTestsFromTestCase(TestModuleImports))
    suite.addTests(loader.loadTestsFromTestCase(TestDatabaseAndRepositories))
    suite.addTests(loader.loadTestsFromTestCase(TestBusinessManagers))
    suite.addTests(loader.loadTestsFromTestCase(TestWebAPIEndpoints))

    start_time = time.time()
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    elapsed = time.time() - start_time

    total_tests = result.testsRun
    failures = len(result.failures)
    errors = len(result.errors)
    skipped = len(result.skipped)
    passed = total_tests - (failures + errors + skipped)

    print("\n" + "=" * 75)
    print(" 📊 BÁO CÁO KẾT QUẢ KIỂM THỬ HỆ THỐNG (TEST RESULT SUMMARY)")
    print("=" * 75)
    print(f"  • Tổng số test case đã chạy: {total_tests}")
    print(f"  • Thành công (PASSED)    : {passed} ✅")
    print(f"  • Thất bại (FAILED)      : {failures} ❌")
    print(f"  • Lỗi thực thi (ERRORS)  : {errors} ⚠️")
    print(f"  • Bỏ qua (SKIPPED)       : {skipped} ⏭️")
    print(f"  • Thời gian hoàn thành  : {elapsed:.2f} giây")
    print("-" * 75)

    if result.wasSuccessful():
        print(" 🎉 KẾT QUẢ: TẤT CẢ TEST CASES ĐÃ ĐẠT CHUẨN (ALL TESTS PASSED SUCCESSFUL)! ")
        print("     Hệ thống sẵn sàng vận hành, không phát hiện lỗi import hay crash.")
        print("=" * 75)
        sys.exit(0)
    else:
        print(" 💥 KẾT QUẢ: PHÁT HIỆN LỖI TRONG QUÁ TRÌNH KIỂM THỬ! ")
        print("     Vui lòng kiểm tra các file bị lỗi trong danh sách trên trước khi commit code.")
        print("=" * 75)
        sys.exit(1)

if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    run_suite()
