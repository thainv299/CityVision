"""
Image Similarity Search Service sử dụng ResNet50 pretrained (torchvision).

Cách hoạt động:
1. Load ResNet50 (bỏ lớp classification cuối) → Feature Extractor 2048D
2. Khi search: trích xuất embedding từ ảnh upload
3. So sánh cosine similarity với toàn bộ ảnh trong logs/
4. Trả về top-K ảnh tương tự nhất
"""

import os
import io
import time
import numpy as np
from pathlib import Path
from typing import Optional

# Lazy imports để tránh lỗi khi không có torch
_torch = None
_torchvision = None
_transforms = None
_Image = None


def _ensure_imports():
    """Import torch/torchvision lazily để tránh crash khi khởi động nếu chưa cài."""
    global _torch, _torchvision, _transforms, _Image
    if _torch is None:
        try:
            import torch
            import torchvision.models as models
            import torchvision.transforms as transforms
            from PIL import Image
            _torch = torch
            _torchvision = models
            _transforms = transforms
            _Image = Image
        except ImportError as e:
            raise ImportError(
                f"Thiếu thư viện: {e}. "
                "Vui lòng cài: pip install torch torchvision pillow"
            )


# Các định dạng ảnh được hỗ trợ
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}

# Biến transform chuẩn cho ResNet50
RESNET_TRANSFORM = None


def _get_transform():
    """Tạo transform pipeline cho ResNet50 (lazy)."""
    global RESNET_TRANSFORM
    if RESNET_TRANSFORM is None:
        _ensure_imports()
        RESNET_TRANSFORM = _transforms.Compose([
            _transforms.Resize(256),
            _transforms.CenterCrop(224),
            _transforms.ToTensor(),
            _transforms.Normalize(
                mean=[0.485, 0.456, 0.406],  # ImageNet mean
                std=[0.229, 0.224, 0.225]    # ImageNet std
            ),
        ])
    return RESNET_TRANSFORM


class ImageSearchService:
    """
    Service tìm kiếm ảnh tương tự sử dụng ResNet50 feature extractor.
    
    Attributes:
        model: ResNet50 model (không có lớp FC cuối)
        device: 'cuda' hoặc 'cpu'
        _embedding_cache: dict {file_path: embedding_vector}
    """

    def __init__(self, logs_dir: str = None, use_gpu: bool = True):
        """
        Khởi tạo service.
        
        Args:
            logs_dir: Thư mục gốc chứa ảnh cần index (mặc định: project_root/logs)
            use_gpu: Có dùng GPU không (True = ưu tiên CUDA nếu có)
        """
        _ensure_imports()

        # Xác định thư mục logs
        if logs_dir is None:
            project_root = Path(__file__).resolve().parent.parent.parent
            self.logs_dir = project_root / "logs"
        else:
            self.logs_dir = Path(logs_dir)

        # Chọn device
        if use_gpu and _torch.cuda.is_available():
            self.device = _torch.device("cuda")
            print(f"[ImageSearch] Sử dụng GPU: {_torch.cuda.get_device_name(0)}")
        else:
            self.device = _torch.device("cpu")
            if use_gpu:
                print("[ImageSearch] Không tìm thấy GPU, fallback sang CPU")
            else:
                print("[ImageSearch] Chạy trên CPU")

        # Load ResNet50 model (bỏ lớp FC classifier cuối)
        self.model = self._load_model()

        # Cache embedding: {absolute_path_str: numpy_vector}
        self._embedding_cache: dict[str, np.ndarray] = {}

        print(f"[ImageSearch] Khởi tạo xong. Logs dir: {self.logs_dir}")

    def _load_model(self):
        """Load ResNet50 pretrained, bỏ lớp FC cuối để lấy 2048D embedding."""
        _ensure_imports()

        # Tải weights pretrained
        weights = _torchvision.ResNet50_Weights.IMAGENET1K_V2
        model = _torchvision.resnet50(weights=weights)

        # Loại bỏ lớp avgpool và fc cuối → lấy feature map trước đó
        # Giữ lại avgpool (global average pooling) nhưng bỏ lớp FC (classifier)
        # Output sẽ là vector 2048 chiều
        model.fc = _torch.nn.Identity()  # Thay FC bằng identity → output 2048D

        model = model.to(self.device)
        model.eval()  # Chế độ inference (không train)

        print("[ImageSearch] Đã load ResNet50 pretrained (IMAGENET1K_V2)")
        return model

    def get_embedding(self, image_source) -> np.ndarray:
        """
        Trích xuất embedding vector 2048D từ một ảnh.
        
        Args:
            image_source: Có thể là:
                - str/Path: đường dẫn file ảnh
                - bytes: nội dung file ảnh (từ upload)
                - PIL.Image: ảnh đã load
        
        Returns:
            numpy array shape (2048,), đã normalize L2
        """
        _ensure_imports()
        transform = _get_transform()

        # Đọc ảnh
        if isinstance(image_source, (str, Path)):
            img = _Image.open(str(image_source)).convert("RGB")
        elif isinstance(image_source, bytes):
            img = _Image.open(io.BytesIO(image_source)).convert("RGB")
        else:
            # Giả sử là PIL Image
            img = image_source.convert("RGB")

        # Áp dụng transform
        tensor = transform(img).unsqueeze(0).to(self.device)  # (1, 3, 224, 224)

        # Forward pass (không tính gradient)
        with _torch.no_grad():
            embedding = self.model(tensor)  # (1, 2048)

        # Chuyển về numpy và normalize L2 (để cosine similarity = dot product)
        vec = embedding.squeeze().cpu().numpy()  # (2048,)
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm

        return vec

    def _get_or_compute_embedding(self, file_path: Path) -> Optional[np.ndarray]:
        """Lấy embedding từ cache hoặc tính mới."""
        key = str(file_path)

        if key not in self._embedding_cache:
            try:
                emb = self.get_embedding(file_path)
                self._embedding_cache[key] = emb
            except Exception as e:
                print(f"[ImageSearch] Lỗi tính embedding {file_path.name}: {e}")
                return None

        return self._embedding_cache[key]

    def index_all_images(self, force_reindex: bool = False) -> int:
        """
        Scan và index toàn bộ ảnh trong logs_dir vào cache.
        
        Args:
            force_reindex: Nếu True, tính lại embedding cho tất cả ảnh (kể cả đã cache)
        
        Returns:
            Số ảnh đã index
        """
        if force_reindex:
            self._embedding_cache.clear()

        if not self.logs_dir.exists():
            print(f"[ImageSearch] Thư mục logs không tồn tại: {self.logs_dir}")
            return 0

        count = 0
        t0 = time.time()

        for file_path in self.logs_dir.rglob("*"):
            if file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            emb = self._get_or_compute_embedding(file_path)
            if emb is not None:
                count += 1

        elapsed = time.time() - t0
        print(f"[ImageSearch] Đã index {count} ảnh trong {elapsed:.2f}s")
        return count

    def search_similar(
        self,
        query_image,
        top_k: int = 12,
        search_dirs: list[str] = None,
        min_score: float = 0.0
    ) -> list[dict]:
        """
        Tìm ảnh tương tự nhất với ảnh query.
        
        Args:
            query_image: Ảnh query (bytes, str path, hoặc PIL Image)
            top_k: Số lượng kết quả trả về
            search_dirs: Danh sách thư mục con trong logs/ cần tìm
                         Ví dụ: ['plates', 'violations']
                         None = tìm tất cả
            min_score: Ngưỡng score tối thiểu (0.0 - 1.0)
        
        Returns:
            List of dict: [
                {
                    'path': 'logs/plates/...',        # đường dẫn tương đối (cho web)
                    'abs_path': '/full/path/...',     # đường dẫn tuyệt đối
                    'score': 0.95,                    # cosine similarity
                    'score_pct': 95.0,                # phần trăm
                    'filename': 'plate_001.jpg',
                    'category': 'plates',             # thư mục cha
                }
            ]
        """
        # Trích xuất embedding từ ảnh query
        try:
            query_emb = self.get_embedding(query_image)
        except Exception as e:
            print(f"[ImageSearch] Lỗi đọc ảnh query: {e}")
            return []

        # Xác định thư mục cần tìm
        if search_dirs:
            scan_roots = [self.logs_dir / d for d in search_dirs]
        else:
            scan_roots = [self.logs_dir]

        # Thu thập tất cả ảnh và tính similarity
        results = []

        for scan_root in scan_roots:
            if not scan_root.exists():
                continue

            for file_path in scan_root.rglob("*"):
                if file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                    continue

                db_emb = self._get_or_compute_embedding(file_path)
                if db_emb is None:
                    continue

                # Cosine similarity (vì đã L2-normalize, = dot product)
                score = float(np.dot(query_emb, db_emb))
                score = max(0.0, min(1.0, score))  # Clamp [0, 1]

                if score >= min_score:
                    # Tính đường dẫn tương đối từ project root
                    try:
                        rel_path = file_path.relative_to(self.logs_dir.parent)
                        web_path = str(rel_path).replace("\\", "/")
                    except ValueError:
                        web_path = str(file_path)

                    # Xác định category (thư mục con trực tiếp trong logs/)
                    try:
                        parts = file_path.relative_to(self.logs_dir).parts
                        category = parts[0] if parts else "unknown"
                    except ValueError:
                        category = "unknown"

                    results.append({
                        "path": web_path,
                        "abs_path": str(file_path),
                        "score": round(score, 4),
                        "score_pct": round(score * 100, 1),
                        "filename": file_path.name,
                        "category": category,
                    })

        # Sắp xếp theo score giảm dần, lấy top_k
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]

    def clear_cache(self):
        """Xóa toàn bộ embedding cache (giải phóng RAM)."""
        self._embedding_cache.clear()
        print("[ImageSearch] Đã xóa embedding cache")

    def get_cache_stats(self) -> dict:
        """Trả về thông tin về cache hiện tại."""
        return {
            "cached_images": len(self._embedding_cache),
            "device": str(self.device),
            "logs_dir": str(self.logs_dir),
        }


# ─── Singleton Instance ────────────────────────────────────────────────────────
# Dùng singleton để tránh load model nhiều lần khi có nhiều request

_service_instance: Optional[ImageSearchService] = None


def get_image_search_service() -> ImageSearchService:
    """Lấy singleton instance của ImageSearchService."""
    global _service_instance
    if _service_instance is None:
        _service_instance = ImageSearchService(use_gpu=True)
    return _service_instance
