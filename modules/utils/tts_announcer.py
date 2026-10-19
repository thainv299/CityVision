import os
import hashlib
import threading
import time
import queue

_pygame_initialized = False
_worker_thread = None
_worker_lock = threading.Lock()
_tts_queue = queue.Queue()

def _ensure_pygame():
    global _pygame_initialized
    if not _pygame_initialized:
        try:
            import pygame
            pygame.mixer.init(frequency=22050, size=-16, channels=1, buffer=512)
            _pygame_initialized = True
        except Exception as e:
            print(f'[TTS] Lỗi khởi tạo pygame.mixer: {e}')

def _get_cache_path(text: str) -> str:
    os.makedirs(os.path.join('logs', 'tts_cache'), exist_ok=True)
    text_hash = hashlib.md5(text.encode('utf-8')).hexdigest()[:12]
    return os.path.join('logs', 'tts_cache', f'{text_hash}.mp3')

def _generate_audio(text: str, cache_path: str) -> bool:
    try:
        from gtts import gTTS
        tts = gTTS(text=text, lang='vi', slow=False)
        tts.save(cache_path)
        return True
    except Exception as e:
        print(f'[TTS] Lỗi tạo audio từ Google TTS: {e}')
        return False

def _safe_print(msg: str):
    """In ra console an toàn trên mọi encoding (Windows cp1252)."""
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode('ascii', errors='replace').decode('ascii'))

def _play_audio(cache_path: str):
    try:
        import pygame
        _ensure_pygame()
        try:
            pygame.mixer.music.load(cache_path)
            pygame.mixer.music.set_volume(1.0)
            pygame.mixer.music.play()
            while pygame.mixer.music.get_busy():
                time.sleep(0.05)
        except Exception as e:
            print(f'[TTS] Lỗi phát audio: {e}')
        finally:
            try:
                pygame.mixer.music.unload()
            except Exception:
                pass
    except Exception as e:
        print(f'[TTS] Lỗi pygame: {e}')

def _worker_loop():
    while True:
        try:
            text, force_regenerate = _tts_queue.get()
            cache_path = _get_cache_path(text)
            if force_regenerate or not os.path.exists(cache_path):
                _safe_print(f'[TTS] Generating audio: "{text[:45]}..."')
                ok = _generate_audio(text, cache_path)
                if not ok:
                    _tts_queue.task_done()
                    continue
            else:
                _safe_print(f'[TTS] Playing: "{text[:45]}..."')

            _play_audio(cache_path)
            # Nghỉ ngắn giữa các thông báo để người nghe phân biệt rõ ràng
            time.sleep(0.5)
        except Exception as e:
            print(f'[TTS] Lỗi worker loop: {e}')
        finally:
            _tts_queue.task_done()

def _ensure_worker():
    global _worker_thread
    with _worker_lock:
        if _worker_thread is None or not _worker_thread.is_alive():
            _worker_thread = threading.Thread(target=_worker_loop, daemon=True, name="TTS-Queue-Worker")
            _worker_thread.start()

def announce(text: str, force_regenerate: bool = False):
    """Thêm câu thông báo vào hàng đợi. Worker thread sẽ phát tuần tự từng câu."""
    if not text:
        return
    _ensure_worker()
    _tts_queue.put((text, force_regenerate))
    qsize = _tts_queue.qsize()
    if qsize > 1:
        _safe_print(f'[TTS] Đã thêm vào hàng đợi (vị trí: {qsize}): "{text[:35]}..."')

def announce_plate_violation(plate: str):
    if not plate or plate.startswith('ID_'):
        text = 'Yêu cầu xe đang dừng đỗ trước cổng trường nhanh chóng di chuyển'
    else:
        plate_spoken = ' '.join(plate.upper())
        text = (
            f'Yêu cầu xe có biển kiểm soát {plate_spoken} '
            f'nhanh chóng di chuyển, không dừng đỗ trước cổng trường'
        )
    announce(text)

