import telebot
import os
import threading
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

bot = None
if TELEGRAM_BOT_TOKEN:
    try:
        bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)
    except Exception as e:
        print(f"[Telegram Bot] Lỗi khởi tạo: {e}")

alert_manager_ref = None

def send_alert_with_button(img_path, caption, level):
    if not bot or not TELEGRAM_CHAT_ID:
        # Im lặng nếu không có bot hoặc chat_id
        return
        
    markup = InlineKeyboardMarkup()
    btn = InlineKeyboardButton(text="Xác nhận ✅", callback_data=f"ack_alert_{level}")
    markup.add(btn)
    
    try:
        with open(img_path, "rb") as photo:
            bot.send_photo(TELEGRAM_CHAT_ID, photo, caption=caption, reply_markup=markup)
    except Exception as e:
        print(f"[Telegram Bot] Lỗi khi gửi ảnh lên Telegram: {e}")

def _dummy_decorator(f):
    return f

decorator = bot.callback_query_handler(func=lambda call: call.data.startswith('ack_alert_')) if bot else _dummy_decorator

@decorator
def ack_alert_callback(call):
    if not bot:
        return
        
    global alert_manager_ref
    
    try:
        acked_level = int(call.data.split('_')[-1])
        message_send_time = call.message.date  # Unix timestamp from Telegram
        
        if alert_manager_ref is not None:
            alert_manager_ref.user_feedback_received(acked_level, message_send_time)
            
        # Sửa tin nhắn gốc để xóa nút bấm và thêm trạng thái xác nhận
        new_caption = (call.message.caption or "") + "\n\n ĐÃ XÁC NHẬN 🟢 "
        bot.edit_message_caption(caption=new_caption, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None)
        
        # Trả lời lại callback query để dừng hiệu ứng vòng chờ loading trên app
        bot.answer_callback_query(call.id, "Đã xác nhận cảnh báo!")
    except Exception as e:
        print(f"[Telegram Bot] Lỗi khi xử lý callback từ Telegram: {e}")

_is_polling_started = False

def start_bot_thread(manager_instance):
    global alert_manager_ref, _is_polling_started
    alert_manager_ref = manager_instance
    
    if not bot:
        return
        
    if _is_polling_started:
        return
        
    _is_polling_started = True
    
    def run_polling():
        try:
            # Use shorter timeouts to avoid hanging too long
            bot.infinity_polling(timeout=10, long_polling_timeout=5)
        except Exception as e:
            print(f"[Telegram Bot] Không thể kết nối polling (Có thể do mạng): {e}")
            
    polling_thread = threading.Thread(target=run_polling, daemon=True)
    polling_thread.start()
    print("[Telegram Bot] Luồng kết nối BOT Telegram đã được khởi động ở chế độ nền.")
