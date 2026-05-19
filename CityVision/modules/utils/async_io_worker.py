"""
AsyncIOWorker — Hàng đợi I/O chạy nền cho pipeline xử lý video.

Mục đích: Tách tất cả tác vụ I/O nặng (ghi file, gửi Telegram, ghi DB)
ra khỏi vòng lặp frame chính, giúp GPU và CPU hoạt động liên tục.

Sử dụng:
    worker = AsyncIOWorker(num_threads=2)
    worker.start()
    worker.enqueue(task_type, **kwargs)   # Tức thì, không blocking
    ...
    worker.shutdown(wait=True)            # Chờ xử lý hết queue trước khi tắt
"""

import os
import csv
import queue
import threading
import traceback
from typing import Any, Callable, Dict, Optional

import cv2
import numpy as np


class AsyncIOWorker:
    """
    Worker chạy nền xử lý các tác vụ I/O nặng qua hàng đợi (queue).
    Hỗ trợ nhiều loại task: ghi ảnh, gửi Telegram, ghi DB, ghi CSV, v.v.
    """

    def __init__(self, num_threads: int = 2, max_queue_size: int = 200):
        """
        Args:
            num_threads: Số worker threads xử lý I/O song song.
                         2 là đủ cho hầu hết trường hợp (1 cho Telegram, 1 cho file/DB).
            max_queue_size: Giới hạn số task trong queue. Khi đầy, task cũ nhất sẽ bị bỏ.
        """
        self._queue: queue.Queue = queue.Queue(maxsize=max_queue_size)
        self._threads: list = []
        self._num_threads = num_threads
        self._running = False
        self._stats = {
            "enqueued": 0,
            "completed": 0,
            "errors": 0,
            "dropped": 0,
        }
        self._lock = threading.Lock()

        # Telegram credentials (được set từ bên ngoài)
        self.telegram_bot_token: str = ""
        self.telegram_chat_id: str = ""

    def start(self):
        """Khởi động các worker threads."""
        if self._running:
            return
        self._running = True
        for i in range(self._num_threads):
            t = threading.Thread(
                target=self._worker_loop,
                name=f"AsyncIO-{i}",
                daemon=True,
            )
            t.start()
            self._threads.append(t)
        print(f"[AsyncIOWorker] Đã khởi động {self._num_threads} worker threads.")

    def shutdown(self, wait: bool = True, timeout: float = 30.0):
        """
        Tắt worker. Nếu wait=True, chờ xử lý hết các task còn trong queue.

        Args:
            wait: Có chờ xử lý hết queue không.
            timeout: Thời gian tối đa chờ (giây).
        """
        self._running = False
        if wait:
            # Đẩy sentinel values để các threads biết cần thoát
            for _ in self._threads:
                try:
                    self._queue.put(None, timeout=1.0)
                except queue.Full:
                    pass
            for t in self._threads:
                t.join(timeout=timeout / max(len(self._threads), 1))
        self._threads.clear()
        remaining = self._queue.qsize()
        if remaining > 0:
            print(f"[AsyncIOWorker] Cảnh báo: còn {remaining} task chưa xử lý khi shutdown.")
        print(f"[AsyncIOWorker] Đã tắt. Stats: {self._stats}")

    @property
    def stats(self) -> Dict[str, int]:
        """Trả về thống kê hoạt động."""
        with self._lock:
            return dict(self._stats)

    @property
    def pending_count(self) -> int:
        """Số task đang chờ trong queue."""
        return self._queue.qsize()

    # ─── ENQUEUE METHODS ──────────────────────────────────────────────

    def enqueue(self, task_type: str, **kwargs):
        """
        Đẩy một task vào queue. Trả về ngay lập tức (không blocking).

        Args:
            task_type: Loại task ("save_image", "telegram_image", "db_write", ...)
            **kwargs: Tham số cho task.
        """
        task = {"type": task_type, **kwargs}
        try:
            self._queue.put_nowait(task)
            with self._lock:
                self._stats["enqueued"] += 1
        except queue.Full:
            with self._lock:
                self._stats["dropped"] += 1

    def enqueue_save_image(self, path: str, image: np.ndarray):
        """Đẩy task ghi ảnh vào queue."""
        # Copy ảnh vì frame gốc có thể bị thay đổi trước khi worker xử lý
        self.enqueue("save_image", path=path, image=image.copy())

    def enqueue_save_video(self, path: str, frames: list, fps: float):
        """Đẩy task ghi video vào queue."""
        self.enqueue("save_video", path=path, frames=frames, fps=fps)

    def enqueue_telegram_image(self, path: str, caption: str,
                                bot_token: str = "", chat_id: str = ""):
        """Đẩy task gửi ảnh Telegram vào queue."""
        self.enqueue(
            "telegram_image",
            path=path,
            caption=caption,
            bot_token=bot_token or self.telegram_bot_token,
            chat_id=chat_id or self.telegram_chat_id,
        )

    def enqueue_telegram_image_from_frame(self, frame: np.ndarray, caption: str,
                                           bot_token: str = "", chat_id: str = ""):
        """Đẩy task ghi ảnh tạm rồi gửi Telegram."""
        self.enqueue(
            "telegram_image_from_frame",
            frame=frame.copy(),
            caption=caption,
            bot_token=bot_token or self.telegram_bot_token,
            chat_id=chat_id or self.telegram_chat_id,
        )

    def enqueue_telegram_video(self, path: str, caption: str,
                                bot_token: str = "", chat_id: str = ""):
        """Đẩy task gửi video Telegram vào queue."""
        self.enqueue(
            "telegram_video",
            path=path,
            caption=caption,
            bot_token=bot_token or self.telegram_bot_token,
            chat_id=chat_id or self.telegram_chat_id,
        )

    def enqueue_traffic_alert(self, level: int, frame: np.ndarray):
        """Đẩy task cảnh báo tắc nghẽn (ghi ảnh + gửi Telegram với nút ACK)."""
        self.enqueue("traffic_alert", level=level, frame=frame.copy())

    def enqueue_csv_append(self, csv_path: str, row: list):
        """Đẩy task ghi 1 dòng vào CSV."""
        self.enqueue("csv_append", csv_path=csv_path, row=row)

    def enqueue_db_write(self, callback: Callable, args: tuple = (), kwargs: dict = None):
        """Đẩy task ghi DB (gọi callback bất kỳ)."""
        self.enqueue("db_write", callback=callback, args=args, kwargs=kwargs or {})

    def enqueue_generic(self, callback: Callable, args: tuple = (), kwargs: dict = None):
        """Đẩy task callback tùy ý."""
        self.enqueue("generic", callback=callback, args=args, kwargs=kwargs or {})

    # ─── WORKER LOOP ──────────────────────────────────────────────────

    def _worker_loop(self):
        """Vòng lặp chính của worker thread."""
        while self._running or not self._queue.empty():
            try:
                task = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue

            if task is None:
                # Sentinel value — thoát thread
                break

            try:
                self._process_task(task)
                with self._lock:
                    self._stats["completed"] += 1
            except Exception as e:
                with self._lock:
                    self._stats["errors"] += 1
                print(f"[AsyncIOWorker] Lỗi xử lý task {task.get('type', '?')}: {e}")
                traceback.print_exc()
            finally:
                self._queue.task_done()

    def _process_task(self, task: Dict[str, Any]):
        """Xử lý 1 task dựa trên type."""
        task_type = task.get("type")

        if task_type == "save_image":
            self._do_save_image(task)
        elif task_type == "save_video":
            self._do_save_video(task)
        elif task_type == "telegram_image":
            self._do_telegram_image(task)
        elif task_type == "telegram_image_from_frame":
            self._do_telegram_image_from_frame(task)
        elif task_type == "telegram_video":
            self._do_telegram_video(task)
        elif task_type == "traffic_alert":
            self._do_traffic_alert(task)
        elif task_type == "csv_append":
            self._do_csv_append(task)
        elif task_type == "db_write":
            self._do_db_write(task)
        elif task_type == "generic":
            self._do_generic(task)
        else:
            print(f"[AsyncIOWorker] Task type không hợp lệ: {task_type}")

    # ─── TASK HANDLERS ────────────────────────────────────────────────

    def _do_save_image(self, task: Dict[str, Any]):
        path = task["path"]
        image = task["image"]
        os.makedirs(os.path.dirname(path), exist_ok=True)
        cv2.imwrite(path, image)

    def _do_save_video(self, task: Dict[str, Any]):
        path = task["path"]
        frames = task["frames"]
        fps = task.get("fps", 30.0)
        if not frames:
            return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fh, fw = frames[0].shape[:2]
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        out = cv2.VideoWriter(path, fourcc, fps, (fw, fh))
        for f in frames:
            out.write(f)
        out.release()

    def _do_telegram_image(self, task: Dict[str, Any]):
        from modules.utils.telegram_bot import send_telegram_image
        send_telegram_image(
            task["path"],
            task["caption"],
            task.get("bot_token", self.telegram_bot_token),
            task.get("chat_id", self.telegram_chat_id),
        )

    def _do_telegram_image_from_frame(self, task: Dict[str, Any]):
        """Ghi ảnh tạm → gửi Telegram → xóa file tạm."""
        from modules.utils.telegram_bot import send_telegram_image
        from modules.utils.common_utils import now_ts

        temp_dir = os.path.join("logs", "violations", "_temp")
        os.makedirs(temp_dir, exist_ok=True)
        img_path = os.path.join(temp_dir, f"temp_alert_{now_ts()}.jpg")
        cv2.imwrite(img_path, task["frame"])
        send_telegram_image(
            img_path,
            task["caption"],
            task.get("bot_token", self.telegram_bot_token),
            task.get("chat_id", self.telegram_chat_id),
        )
        try:
            os.remove(img_path)
        except OSError:
            pass

    def _do_telegram_video(self, task: Dict[str, Any]):
        from modules.utils.telegram_bot import send_telegram_video
        send_telegram_video(
            task["path"],
            task["caption"],
            task.get("bot_token", self.telegram_bot_token),
            task.get("chat_id", self.telegram_chat_id),
        )

    def _do_traffic_alert(self, task: Dict[str, Any]):
        """Ghi ảnh cảnh báo tắc nghẽn → gửi Telegram có nút ACK."""
        from modules.utils.interactive_telegram_bot import send_alert_with_button

        level = task["level"]
        frame = task["frame"]
        img_path = "logs/traffic_alert.jpg"
        cv2.imwrite(img_path, frame)

        caption = ""
        if level == 1:
            caption = "CẢNH BÁO ⚠️: Giao thông đang Bắt Đầu Đông (Mức 1)."
        elif level == 2:
            caption = "CẢNH BÁO ⚠️: Giao thông đang RẤT ĐÔNG (Mức 2)."
        elif level == 3:
            caption = "BÁO ĐỘNG 🚨: TẮC NGHẼN nghiêm trọng (Mức 3)!"

        send_alert_with_button(img_path, caption, level)

    def _do_csv_append(self, task: Dict[str, Any]):
        csv_path = task["csv_path"]
        row = task["row"]
        with open(csv_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(row)

    def _do_db_write(self, task: Dict[str, Any]):
        callback = task["callback"]
        args = task.get("args", ())
        kwargs = task.get("kwargs", {})
        callback(*args, **kwargs)

    def _do_generic(self, task: Dict[str, Any]):
        callback = task["callback"]
        args = task.get("args", ())
        kwargs = task.get("kwargs", {})
        callback(*args, **kwargs)
