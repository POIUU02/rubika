from flask import Flask, request, render_template, jsonify
import subprocess
import tempfile
import os
import requests
import json
import time
import sys
import re
import shutil
import importlib.util
import logging
import traceback
import sqlite3
from datetime import datetime, timedelta
import threading
import secrets
import string

app = Flask(__name__)

PORT = int(os.environ.get('PORT', 8080))
MAX_CODE_SIZE = 500 * 1024
ADMIN_ID = 6443963679
DB_PATH = "bot_data.db"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ============================================================
# ===== دیتابیس =====
# ============================================================

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # جدول کاربران
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            telegram_id TEXT,
            balance INTEGER DEFAULT 0,
            is_active BOOLEAN DEFAULT 1,
            created_at DATETIME
        )
    ''')
    
    # جدول اشتراک‌ها (ساب)
    c.execute('''
        CREATE TABLE IF NOT EXISTS subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            link_token TEXT UNIQUE,
            access_code TEXT,
            storage_limit REAL,
            time_limit TEXT,
            expires_at DATETIME,
            is_active BOOLEAN DEFAULT 1,
            created_at DATETIME,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')
    
    # جدول ربات‌های فعال
    c.execute('''
        CREATE TABLE IF NOT EXISTS active_bots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            bot_token TEXT,
            bot_code TEXT,
            subscription_id INTEGER,
            status TEXT DEFAULT 'running',
            created_at DATETIME,
            expires_at DATETIME,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')
    
    # جدول رسیدها
    c.execute('''
        CREATE TABLE IF NOT EXISTS receipts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount INTEGER,
            receipt_image TEXT,
            status TEXT DEFAULT 'pending',
            created_at DATETIME,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')
    
    # جدول تیکت‌ها
    c.execute('''
        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            subject TEXT,
            message TEXT,
            status TEXT DEFAULT 'pending',
            created_at DATETIME,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')
    
    # جدول تراکنش‌ها
    c.execute('''
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            type TEXT,
            amount INTEGER,
            description TEXT,
            created_at DATETIME,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')
    
    # جدول قیمت‌ها (پلن‌ها)
    c.execute('''
        CREATE TABLE IF NOT EXISTS plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            storage REAL,
            duration TEXT,
            price INTEGER,
            is_active BOOLEAN DEFAULT 1
        )
    ''')
    
    # اضافه کردن پلن‌های پیش‌فرض
    default_plans = [
        ('عادی', 1, '1 روز', 2000),
        ('ویژه', 2, '3 روز', 5000),
        ('طلایی', 5, '7 روز', 12000),
        ('الماس', 10, '30 روز', 50000)
    ]
    for plan in default_plans:
        c.execute('SELECT * FROM plans WHERE name = ?', (plan[0],))
        if not c.fetchone():
            c.execute('INSERT INTO plans (name, storage, duration, price, is_active) VALUES (?, ?, ?, ?, ?)',
                      (plan[0], plan[1], plan[2], plan[3], 1))
    
    conn.commit()
    conn.close()
    logger.info("✅ دیتابیس آماده شد")

init_db()

# ============================================================
# ===== کد ربات مدیریت =====
# ============================================================

BOT_TOKEN = "8262116870:AAGhf7siH7qpVm4nPAM8kGJcPwgyu0PZZFo"

TELEGRAM_BOT_CODE = f'''
import os
import sys
import time
import re
import logging
import sqlite3
import secrets
import string
import json
import requests
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
import subprocess
import tempfile

TOKEN = "{BOT_TOKEN}"
ADMIN_ID = {ADMIN_ID}
DB_PATH = "bot_data.db"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ===== توابع دیتابیس =====
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def generate_link_token():
    return secrets.token_urlsafe(16)

def generate_access_code():
    alphabet = string.ascii_uppercase + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(8))

def parse_time_limit(time_str):
    time_str = time_str.strip().lower()
    units = { 'ثانیه': 1, 'دقیقه': 60, 'ساعت': 3600, 'روز': 86400, 'هفته': 604800, 'ماه': 2592000, 'سال': 31536000 }
    for unit, seconds in units.items():
        if unit in time_str:
            number = re.search(r'(\\d+\\.?\\d*)', time_str)
            if number:
                return float(number.group(1)) * seconds
    return None

def create_user(username, telegram_id=None):
    conn = get_db()
    c = conn.cursor()
    try:
        c.execute('INSERT INTO users (username, telegram_id, created_at) VALUES (?, ?, ?)',
                  (username, telegram_id, datetime.now().isoformat()))
        user_id = c.lastrowid
        conn.commit()
        return user_id
    except:
        return None
    finally:
        conn.close()

def create_subscription(user_id, username, storage_limit, time_limit):
    conn = get_db()
    c = conn.cursor()
    link_token = generate_link_token()
    access_code = generate_access_code()
    seconds = parse_time_limit(time_limit)
    expires_at = datetime.now() + timedelta(seconds=seconds) if seconds else datetime.now() + timedelta(days=1)
    c.execute('INSERT INTO subscriptions (user_id, username, link_token, access_code, storage_limit, time_limit, expires_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
              (user_id, username, link_token, access_code, storage_limit, time_limit, expires_at.isoformat(), datetime.now().isoformat()))
    sub_id = c.lastrowid
    conn.commit()
    conn.close()
    return {{ 'id': sub_id, 'link_token': link_token, 'access_code': access_code, 'storage_limit': storage_limit, 'time_limit': time_limit, 'expires_at': expires_at }}

def get_user_by_username(username):
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM users WHERE username = ?', (username,))
    result = c.fetchone()
    conn.close()
    return result

def get_all_users():
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM users ORDER BY created_at DESC')
    result = c.fetchall()
    conn.close()
    return result

def get_all_subscriptions():
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM subscriptions ORDER BY created_at DESC')
    result = c.fetchall()
    conn.close()
    return result

def update_subscription_time(sub_id, new_time_limit):
    conn = get_db()
    c = conn.cursor()
    seconds = parse_time_limit(new_time_limit)
    expires_at = datetime.now() + timedelta(seconds=seconds) if seconds else datetime.now() + timedelta(days=1)
    c.execute('UPDATE subscriptions SET time_limit = ?, expires_at = ? WHERE id = ?',
              (new_time_limit, expires_at.isoformat(), sub_id))
    conn.commit()
    conn.close()
    return True

def delete_subscription(sub_id):
    conn = get_db()
    c = conn.cursor()
    c.execute('DELETE FROM subscriptions WHERE id = ?', (sub_id,))
    conn.commit()
    conn.close()
    return True

def extract_token_from_code(code):
    patterns = [r'TOKEN\\s*=\\s*["\\']([^"\\']+)["\\']', r'token\\s*=\\s*["\\']([^"\\']+)["\\']', r'BOT_TOKEN\\s*=\\s*["\\']([^"\\']+)["\\']', r'API_TOKEN\\s*=\\s*["\\']([^"\\']+)["\\']']
    for pattern in patterns:
        match = re.search(pattern, code)
        if match:
            return match.group(1)
    return None

def check_token_valid(token):
    try:
        url = f"https://api.telegram.org/bot{{token}}/getMe"
        resp = requests.get(url, timeout=5)
        return resp.status_code == 200
    except:
        return False

def run_user_bot(code, token):
    try:
        temp_dir = tempfile.mkdtemp()
        temp_path = os.path.join(temp_dir, "user_bot.py")
        code_with_token = re.sub(r'TOKEN\\s*=\\s*["\\'][^"\\']*["\\']', f'TOKEN = "{{token}}"', code)
        if code_with_token == code:
            code_with_token = f'TOKEN = "{{token}}"\\n\\n' + code
        with open(temp_path, 'w', encoding='utf-8') as f:
            f.write(code_with_token)
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["TOKEN"] = token
        process = subprocess.Popen([sys.executable, temp_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, cwd=temp_dir)
        return True, "ربات با موفقیت اجرا شد!"
    except Exception as e:
        return False, f"خطا در اجرا: {{e}}"

# ===== کیبوردها =====
def main_menu_keyboard():
    keyboard = [
        [InlineKeyboardButton("👤 مدیریت کاربران", callback_data="users_menu")],
        [InlineKeyboardButton("📦 مدیریت اشتراک‌ها", callback_data="subs_menu")],
        [InlineKeyboardButton("🤖 مدیریت ربات‌ها", callback_data="bots_menu")],
        [InlineKeyboardButton("💰 مدیریت مالی", callback_data="finance_menu")],
        [InlineKeyboardButton("📊 گزارشات", callback_data="reports_menu")],
        [InlineKeyboardButton("⚙️ تنظیمات", callback_data="settings_menu")],
        [InlineKeyboardButton("📞 پشتیبانی", callback_data="support_menu")],
        [InlineKeyboardButton("ℹ️ راهنما", callback_data="help")],
    ]
    return InlineKeyboardMarkup(keyboard)

def users_menu_keyboard():
    keyboard = [
        [InlineKeyboardButton("➕ ایجاد کاربر جدید", callback_data="add_user")],
        [InlineKeyboardButton("📋 لیست کاربران", callback_data="list_users")],
        [InlineKeyboardButton("✏️ ویرایش کاربر", callback_data="edit_user")],
        [InlineKeyboardButton("🗑️ حذف کاربر", callback_data="delete_user")],
        [InlineKeyboardButton("🔍 جستجوی کاربر", callback_data="search_user")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="back_main")],
    ]
    return InlineKeyboardMarkup(keyboard)

def subs_menu_keyboard():
    keyboard = [
        [InlineKeyboardButton("➕ ایجاد اشتراک جدید", callback_data="add_sub")],
        [InlineKeyboardButton("📋 لیست اشتراک‌ها", callback_data="list_subs")],
        [InlineKeyboardButton("🔄 تمدید اشتراک", callback_data="renew_sub")],
        [InlineKeyboardButton("🗑️ حذف اشتراک", callback_data="delete_sub")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="back_main")],
    ]
    return InlineKeyboardMarkup(keyboard)

def bots_menu_keyboard():
    keyboard = [
        [InlineKeyboardButton("📤 دریافت فایل کد", callback_data="get_bot_file")],
        [InlineKeyboardButton("📋 لیست ربات‌های فعال", callback_data="list_bots")],
        [InlineKeyboardButton("🔄 تمدید ربات", callback_data="renew_bot")],
        [InlineKeyboardButton("🗑️ حذف ربات", callback_data="delete_bot")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="back_main")],
    ]
    return InlineKeyboardMarkup(keyboard)

def finance_menu_keyboard():
    keyboard = [
        [InlineKeyboardButton("💰 کیف پول کاربران", callback_data="wallets")],
        [InlineKeyboardButton("➕ افزایش موجودی", callback_data="add_balance")],
        [InlineKeyboardButton("📜 تاریخچه تراکنش‌ها", callback_data="transactions")],
        [InlineKeyboardButton("📩 رسیدها", callback_data="receipts")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="back_main")],
    ]
    return InlineKeyboardMarkup(keyboard)

def reports_menu_keyboard():
    keyboard = [
        [InlineKeyboardButton("📊 آمار کلی", callback_data="stats")],
        [InlineKeyboardButton("📈 گزارش فعالیت", callback_data="activity_report")],
        [InlineKeyboardButton("📉 گزارش مصرف", callback_data="usage_report")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="back_main")],
    ]
    return InlineKeyboardMarkup(keyboard)

def settings_menu_keyboard():
    keyboard = [
        [InlineKeyboardButton("💰 مدیریت قیمت‌ها", callback_data="manage_prices")],
        [InlineKeyboardButton("📝 پیام‌های سیستمی", callback_data="system_messages")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="back_main")],
    ]
    return InlineKeyboardMarkup(keyboard)

def support_menu_keyboard():
    keyboard = [
        [InlineKeyboardButton("📩 دریافت تیکت", callback_data="get_ticket")],
        [InlineKeyboardButton("📋 لیست تیکت‌ها", callback_data="list_tickets")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="back_main")],
    ]
    return InlineKeyboardMarkup(keyboard)

# ===== دستورات ربات =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if str(user.id) != str(ADMIN_ID):
        await update.message.reply_text("❌ شما دسترسی به این ربات ندارید!")
        return
    await update.message.reply_text("🤖 **ربات مدیریت پنل**\\n\\nسلام ادمین عزیز! به پنل مدیریت خوش آمدید.\\nلطفاً یکی از گزینه‌های زیر را انتخاب کنید:", reply_markup=main_menu_keyboard())

async def add_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("👤 **ایجاد کاربر جدید**\\n\\nلطفاً نام کاربری را وارد کنید:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="users_menu")]]))
    context.user_data['action'] = 'add_user_username'

async def handle_add_user_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = update.message.text.strip()
    context.user_data['new_username'] = username
    context.user_data['action'] = 'add_user_time'
    await update.message.reply_text(f"👤 نام کاربری: {{username}}\\n\\n⏰ **محدودیت زمان** را وارد کنید:\\nمثال: ۱ساعت، ۲روز، ۳ماه، ۱سال", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="users_menu")]]))

async def handle_add_user_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    time_limit = update.message.text.strip()
    context.user_data['new_time'] = time_limit
    context.user_data['action'] = 'add_user_storage'
    await update.message.reply_text(f"⏰ زمان: {{time_limit}}\\n\\n💾 **محدودیت حافظه** را به گیگابایت وارد کنید:\\nمثال: ۱ (یعنی ۱ گیگابایت) یا ۰.۵ (یعنی ۵۰۰ مگابایت)", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="users_menu")]]))

async def handle_add_user_storage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        storage = float(update.message.text.strip().replace(',', '.'))
    except:
        await update.message.reply_text("❌ عدد معتبر وارد کنید!")
        return
    username = context.user_data.get('new_username')
    time_limit = context.user_data.get('new_time')
    user_id = create_user(username)
    if not user_id:
        await update.message.reply_text("❌ این نام کاربری قبلاً ثبت شده است!")
        context.user_data['action'] = None
        return
    sub = create_subscription(user_id, username, storage, time_limit)
    await update.message.reply_text(f"✅ **کاربر با موفقیت ساخته شد!**\\n\\n👤 نام کاربری: {{username}}\\n⏰ زمان: {{time_limit}}\\n💾 حافظه: {{storage}} گیگابایت\\n\\n🔗 **لینک:** https://yourdomain.com/user/{{sub['link_token']}}\\n🔑 **رمز:** `{{sub['access_code']}}`\\n\\n💡 این اطلاعات را به کاربر ارسال کنید.", parse_mode='Markdown')
    context.user_data['action'] = None

async def list_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    users = get_all_users()
    if not users:
        await query.edit_message_text("📋 هیچ کاربری ثبت نشده است.")
        return
    text = "📋 **لیست کاربران:**\\n\\n"
    for user in users[:20]:
        text += f"👤 {{user['username']}} | {{'✅ فعال' if user['is_active'] else '❌ غیرفعال'}}\\n   📅 {{user['created_at'][:10]}}\\n\\n"
    if len(users) > 20:
        text += f"... و {{len(users) - 20}} کاربر دیگر"
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="users_menu")]]))

# ===== ادامه کد ربات (ادامه دارد) =====
# ... (بقیه توابع ربات مثل مدیریت اشتراک‌ها، مدیریت ربات‌ها و ... به همین شکل)

def run_admin_bot():
    """اجرای ربات در پس‌زمینه"""
    while True:
        try:
            from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
            
            app_bot = Application.builder().token(BOT_TOKEN).build()
            app_bot.add_handler(CommandHandler("start", start))
            app_bot.add_handler(CallbackQueryHandler(button_handler))
            app_bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
            app_bot.add_handler(MessageHandler(filters.Document.ALL, handle_bot_file))
            
            logger.info("🚀 ربات مدیریت پنل شروع به کار کرد...")
            app_bot.run_polling()
        except Exception as e:
            logger.error(f"❌ خطا در ربات: {e}")
            time.sleep(5)

# ============================================================
# ===== توابع سایت =====
# ============================================================

def check_telegram_token(token):
    try:
        url = f"https://api.telegram.org/bot{token}/getMe"
        resp = requests.get(url, timeout=5)
        return resp.status_code == 200
    except:
        return False

def execute_user_bot(user_code, user_token):
    # ... (کد اجرای ربات کاربر)
    pass

# ============================================================
# ===== مسیرهای سایت =====
# ============================================================

@app.route('/', methods=['GET', 'POST'])
def index():
    # ... (صفحه اصلی سایت)
    return render_template('index.html')

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'online', 'python': sys.version, 'timestamp': time.time()})

# ============================================================
# ===== اجرای اصلی =====
# ============================================================

if __name__ == '__main__':
    # اجرای ربات در پس‌زمینه
    bot_thread = threading.Thread(target=run_admin_bot, daemon=True)
    bot_thread.start()
    
    port = int(os.environ.get('PORT', 8080))
    print("\n" + "="*60)
    print("🤖 سیستم مدیریت ربات‌ها (سایت + ربات ادمین)")
    print("="*60)
    print(f"📡 پورت: {port}")
    print("🌐 آدرس: http://localhost:" + str(port))
    print("🤖 ربات ادمین: در حال اجرا...")
    print("="*60 + "\n")
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
