
import os
import threading
import cv2
import numpy as np

# ─── Kiểm tra CUDA availability ────────────────────────────────────
_USE_CUDA = os.environ.get("USE_OPENCV_CUDA", "0") == "1"
_cuda_available = False

if _USE_CUDA:
    try:
        device_count = cv2.cuda.getCudaEnabledDeviceCount()
        if device_count > 0:
            _cuda_available = True
            print(f"[GPU-CV] ✅ CUDA enabled — {device_count} device(s) detected.")
        else:
            print("[GPU-CV] ⚠️ USE_OPENCV_CUDA=1 nhưng không tìm thấy GPU, fallback CPU.")
    except Exception:
        print("[GPU-CV] ⚠️ OpenCV không được build với CUDA, fallback CPU.")


def is_cuda_enabled() -> bool:
    """Kiểm tra CUDA có đang được sử dụng không."""
    return _cuda_available


# ─── Thread-local GpuMat (tránh xung đột giữa các luồng) ──────────
_tls = threading.local()


def _get_gpu_mat(name: str = "src") -> "cv2.cuda_GpuMat":
    """Lấy GpuMat từ thread-local storage, tạo mới nếu chưa có."""
    key = f"gpu_{name}"
    if not hasattr(_tls, key):
        setattr(_tls, key, cv2.cuda_GpuMat())
    return getattr(_tls, key)


# ─── Cached CUDA objects (per-thread) ──────────────────────────────
def _get_clahe(clip_limit: float, tile_grid_size: tuple):
    """Lấy hoặc tạo CLAHE CUDA object (cache per-thread)."""
    key = f"clahe_{clip_limit}_{tile_grid_size}"
    if not hasattr(_tls, key):
        setattr(_tls, key, cv2.cuda.createCLAHE(clip_limit, tile_grid_size))
    return getattr(_tls, key)


# ═══════════════════════════════════════════════════════════════════
#  CÁC HÀM WRAPPER — Drop-in replacement cho cv2.*
# ═══════════════════════════════════════════════════════════════════

def resize(src: np.ndarray, dsize: tuple, interpolation: int = cv2.INTER_LINEAR) -> np.ndarray:
    """Thay thế cv2.resize(). Trên GPU dùng cv2.cuda.resize()."""
    if _cuda_available:
        try:
            gpu_src = _get_gpu_mat("resize_src")
            gpu_src.upload(src)
            gpu_dst = cv2.cuda.resize(gpu_src, dsize, interpolation=interpolation)
            return gpu_dst.download()
        except Exception:
            pass  # Fallback CPU nếu lỗi
    return cv2.resize(src, dsize, interpolation=interpolation)


def cvtColor(src: np.ndarray, code: int) -> np.ndarray:
    """Thay thế cv2.cvtColor(). Trên GPU dùng cv2.cuda.cvtColor()."""
    if _cuda_available:
        try:
            gpu_src = _get_gpu_mat("cvt_src")
            gpu_src.upload(src)
            gpu_dst = cv2.cuda.cvtColor(gpu_src, code)
            return gpu_dst.download()
        except Exception:
            pass
    return cv2.cvtColor(src, code)


def GaussianBlur(src: np.ndarray, ksize: tuple, sigmaX: float) -> np.ndarray:
    """Thay thế cv2.GaussianBlur(). Trên GPU dùng cv2.cuda.createGaussianFilter()."""
    if _cuda_available:
        try:
            gpu_src = _get_gpu_mat("blur_src")
            gpu_src.upload(src)
            # createGaussianFilter(srcType, dstType, ksize, sigma1)
            filt = cv2.cuda.createGaussianFilter(src.dtype, -1, ksize, sigmaX)
            gpu_dst = filt.apply(gpu_src)
            return gpu_dst.download()
        except Exception:
            pass
    return cv2.GaussianBlur(src, ksize, sigmaX)


def warpPerspective(src: np.ndarray, M: np.ndarray, dsize: tuple) -> np.ndarray:
    """Thay thế cv2.warpPerspective(). Trên GPU dùng cv2.cuda.warpPerspective()."""
    if _cuda_available:
        try:
            gpu_src = _get_gpu_mat("warp_src")
            gpu_src.upload(src)
            gpu_dst = cv2.cuda.warpPerspective(gpu_src, M, dsize)
            return gpu_dst.download()
        except Exception:
            pass
    return cv2.warpPerspective(src, M, dsize)


def applyCLAHE(src: np.ndarray, clipLimit: float = 2.0, tileGridSize: tuple = (8, 8)) -> np.ndarray:
    """Thay thế cv2.createCLAHE().apply(). Trên GPU dùng cv2.cuda.createCLAHE()."""
    if _cuda_available:
        try:
            gpu_src = _get_gpu_mat("clahe_src")
            gpu_src.upload(src)
            clahe = _get_clahe(clipLimit, tileGridSize)
            gpu_dst = clahe.apply(gpu_src, cv2.cuda.Stream.Null())
            return gpu_dst.download()
        except Exception:
            pass
    clahe = cv2.createCLAHE(clipLimit=clipLimit, tileGridSize=tileGridSize)
    return clahe.apply(src)


def bitwise_and(src1: np.ndarray, src2: np.ndarray) -> np.ndarray:
    """Thay thế cv2.bitwise_and(). Trên GPU dùng cv2.cuda.bitwise_and()."""
    if _cuda_available:
        try:
            gpu_src1 = _get_gpu_mat("bw_src1")
            gpu_src2 = _get_gpu_mat("bw_src2")
            gpu_src1.upload(src1)
            gpu_src2.upload(src2)
            gpu_dst = cv2.cuda.bitwise_and(gpu_src1, gpu_src2)
            return gpu_dst.download()
        except Exception:
            pass
    return cv2.bitwise_and(src1, src2)


def addWeighted(src1: np.ndarray, alpha: float, src2: np.ndarray, beta: float, gamma: float) -> np.ndarray:
    """Thay thế cv2.addWeighted(). Trên GPU dùng cv2.cuda.addWeighted()."""
    if _cuda_available:
        try:
            gpu_src1 = _get_gpu_mat("aw_src1")
            gpu_src2 = _get_gpu_mat("aw_src2")
            gpu_src1.upload(src1)
            gpu_src2.upload(src2)
            gpu_dst = cv2.cuda.addWeighted(gpu_src1, alpha, gpu_src2, beta, gamma)
            return gpu_dst.download()
        except Exception:
            pass
    return cv2.addWeighted(src1, alpha, src2, beta, gamma)
