from flask import Flask, request, render_template, jsonify, session, redirect, url_for, flash
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
import secrets
import string
import threading
from datetime import datetime, timedelta
from functools import wraps

app = Flask(__name__)
app.secret_key = 'your-secret-key-here-change-in-production'

# ==================== تنظیمات ====================
PORT = int(os.environ.get('PORT', 8080))
MAX_CODE_SIZE = 500 * 1024  # 500 کیلوبایت
ADMIN_ID = 6443963679  # آیدی عددی شما
ADMIN_BOT_TOKEN = "8262116870:AAGhf7siH7qpVm4nPAM8kGJcPwgyu0PZZFo"

# ==================== لاگ ====================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ==================== دیتابیس ====================
DB_PATH = "bot_data.db"

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    
    # ===== جدول کاربران =====
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            telegram_id TEXT,
            password TEXT,
            balance INTEGER DEFAULT 0,
            is_active BOOLEAN DEFAULT 1,
            created_at DATETIME
        )
    ''')
    
    # ===== جدول اشتراک‌ها (ساب) =====
    c.execute('''
        CREATE TABLE IF NOT EXISTS subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            link_token TEXT UNIQUE,
            access_code TEXT,
            storage_limit REAL,
            time_limit TEXT,
            time_limit_seconds INTEGER,
            expires_at DATETIME,
            is_active BOOLEAN DEFAULT 1,
            created_at DATETIME,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')
    
    # ===== جدول ربات‌های کاربران =====
    c.execute('''
        CREATE TABLE IF NOT EXISTS user_bots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            bot_token TEXT,
            bot_code TEXT,
            status TEXT DEFAULT 'stopped',
            is_permanent BOOLEAN DEFAULT 0,
            created_at DATETIME,
            expires_at DATETIME,
            last_run DATETIME,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')
    
    # ===== جدول رسیدها =====
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
    
    # ===== جدول پلن‌ها =====
    c.execute('''
        CREATE TABLE IF NOT EXISTS plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            storage INTEGER,
            duration INTEGER,
            price INTEGER,
            is_active BOOLEAN DEFAULT 1
        )
    ''')
    
    # ===== جدول تراکنش‌ها =====
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
    
    # ===== اضافه کردن پلن‌های پیش‌فرض =====
    plans = [
        ('عادی', 1, 1, 2000),
        ('ویژه', 2, 3, 5000),
        ('طلایی', 5, 7, 12000),
        ('الماس', 10, 30, 50000)
    ]
    for plan in plans:
        c.execute('SELECT * FROM plans WHERE name = ?', (plan[0],))
        if not c.fetchone():
            c.execute('INSERT INTO plans (name, storage, duration, price, is_active) VALUES (?, ?, ?, ?, ?)',
                      (plan[0], plan[1], plan[2], plan[3], 1))
    
    conn.commit()
    conn.close()
    logger.info("✅ دیتابیس آماده است")

init_db()

# ==================== توابع کمکی دیتابیس ====================

def generate_link_token():
    return secrets.token_urlsafe(16)

def generate_access_code():
    alphabet = string.ascii_uppercase + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(8))

def parse_time_limit(time_str):
    """تبدیل رشته زمان به ثانیه"""
    time_str = time_str.strip().lower()
    units = {
        'ثانیه': 1,
        'دقیقه': 60,
        'ساعت': 3600,
        'روز': 86400,
        'هفته': 604800,
        'ماه': 2592000,
        'سال': 31536000
    }
    
    # استخراج عدد و واحد
    match = re.match(r'(\d+\.?\d*)\s*([^\d]+)', time_str)
    if not match:
        return None
    
    number = float(match.group(1))
    unit = match.group(2).strip()
    
    for key, value in units.items():
        if key in unit:
            return int(number * value)
    
    return None

def format_time(seconds):
    """تبدیل ثانیه به رشته خوانا"""
    if seconds >= 86400:
        days = seconds // 86400
        hours = (seconds % 86400) // 3600
        return f"{days} روز و {hours} ساعت"
    elif seconds >= 3600:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        return f"{hours} ساعت و {minutes} دقیقه"
    elif seconds >= 60:
        minutes = seconds // 60
        seconds_left = seconds % 60
        return f"{minutes} دقیقه و {seconds_left} ثانیه"
    else:
        return f"{seconds} ثانیه"

def check_subscription_valid(user_id):
    """بررسی اعتبار اشتراک کاربر"""
    conn = get_db()
    c = conn.cursor()
    c.execute('''
        SELECT * FROM subscriptions 
        WHERE user_id = ? AND is_active = 1 AND expires_at > datetime('now')
        ORDER BY expires_at DESC LIMIT 1
    ''', (user_id,))
    result = c.fetchone()
    conn.close()
    return result

# ==================== تابع اجرای ربات کاربر ====================

def inject_token_to_code(code, token):
    token_vars = ['TOKEN', 'token', 'YOUR_TOKEN', 'your_token', 'BOT_TOKEN', 'bot_token', 'API_TOKEN', 'api_token']
    modified_code = code
    for var in token_vars:
        modified_code = re.sub(rf'{var}\s*=\s*["\']([^"\']*)["\']', f'{var} = "{token}"', modified_code)
        modified_code = re.sub(rf'{var}\s*=\s*\'([^\']*)\'', f'{var} = "{token}"', modified_code)
    if 'TOKEN' not in modified_code and 'token' not in modified_code:
        modified_code = f'TOKEN = "{token}"\n\n' + modified_code
    return modified_code

def execute_user_bot(code, token, user_id, is_permanent=False):
    logger.info(f"🔄 شروع اجرای ربات کاربر {user_id}...")
    
    # بررسی اشتراک
    subscription = check_subscription_valid(user_id)
    if not subscription:
        return {'success': False, 'message': '❌ اشتراک شما منقضی شده یا فعال نیست!', 'logs': ''}
    
    # تزریق توکن
    code_with_token = inject_token_to_code(code, token)
    
    temp_dir = tempfile.mkdtemp()
    temp_path = os.path.join(temp_dir, "user_bot.py")
    
    try:
        with open(temp_path, 'w', encoding='utf-8') as f:
            f.write(code_with_token)
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return {'success': False, 'message': f'❌ خطا: {str(e)}', 'logs': str(e)}
    
    # نصب وابستگی‌ها
    install_logs = ""
    try:
        subprocess.run([sys.executable, "-m", "pip", "install", "python-telegram-bot", "--quiet"], 
                      capture_output=True, timeout=60)
        install_logs = "✅ python-telegram-bot نصب شد.\n"
    except:
        install_logs = "⚠️ خطا در نصب python-telegram-bot\n"
    
    # اجرا
    output = ""
    success = False
    error_msg = ""
    process = None
    
    try:
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["TOKEN"] = token
        
        process = subprocess.Popen(
            [sys.executable, temp_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            cwd=temp_dir
        )
        
        stdout, stderr = process.communicate()
        output = stdout + stderr
        success = process.returncode == 0
        
        if not success and stderr:
            error_msg = stderr[:500]
            logger.error(f"❌ خطای اجرا: {error_msg}")
            
    except Exception as e:
        output = f"❌ خطا: {str(e)}"
        success = False
        error_msg = str(e)
        logger.error(f"❌ خطا در اجرا: {e}")
        
    finally:
        try:
            if process and process.poll() is None:
                process.kill()
        except:
            pass
    
    full_output = install_logs + output if install_logs else output
    
    return {
        'success': success,
        'message': '✅ ربات با موفقیت اجرا شد!' if success else f'❌ خطا: {error_msg}',
        'logs': full_output,
        'error': error_msg if not success else ''
    }

# ==================== کد ربات تلگرام ادمین ====================

ADMIN_BOT_CODE = f'''
import os
import sys
import time
import re
import logging
import sqlite3
import secrets
import string
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
import subprocess
import tempfile

TOKEN = "{ADMIN_BOT_TOKEN}"
ADMIN_ID = {ADMIN_ID}

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DB_PATH = "bot_data.db"

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
    units = {{'ثانیه': 1, 'دقیقه': 60, 'ساعت': 3600, 'روز': 86400, 'هفته': 604800, 'ماه': 2592000, 'سال': 31536000}}
    match = re.match(r'(\\d+\\.?\\d*)\\s*([^\\d]+)', time_str.strip().lower())
    if not match:
        return None
    number = float(match.group(1))
    unit = match.group(2).strip()
    for key, value in units.items():
        if key in unit:
            return int(number * value)
    return None

# ===== توابع مدیریت کاربران =====
def create_user(username, telegram_id=None):
    conn = get_db()
    c = conn.cursor()
    try:
        c.execute('INSERT INTO users (username, telegram_id, created_at) VALUES (?, ?, ?)',
                  (username, telegram_id, datetime.now().isoformat()))
        user_id = c.lastrowid
        conn.commit()
        return user_id
    except sqlite3.IntegrityError:
        return None
    finally:
        conn.close()

def get_all_users():
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM users ORDER BY created_at DESC')
    result = c.fetchall()
    conn.close()
    return result

def delete_user(user_id):
    conn = get_db()
    c = conn.cursor()
    c.execute('DELETE FROM users WHERE id = ?', (user_id,))
    c.execute('DELETE FROM subscriptions WHERE user_id = ?', (user_id,))
    c.execute('DELETE FROM user_bots WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

# ===== توابع مدیریت اشتراک =====
def create_subscription(user_id, username, storage_limit, time_limit_str):
    link_token = generate_link_token()
    access_code = generate_access_code()
    time_seconds = parse_time_limit(time_limit_str)
    expires_at = datetime.now() + timedelta(seconds=time_seconds) if time_seconds else None
    
    conn = get_db()
    c = conn.cursor()
    c.execute('''
        INSERT INTO subscriptions (user_id, username, link_token, access_code, storage_limit, time_limit, time_limit_seconds, expires_at, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (user_id, username, link_token, access_code, storage_limit, time_limit_str, time_seconds, expires_at.isoformat() if expires_at else None, datetime.now().isoformat()))
    conn.commit()
    conn.close()
    
    return link_token, access_code

def get_user_subscriptions(user_id):
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM subscriptions WHERE user_id = ? ORDER BY created_at DESC', (user_id,))
    result = c.fetchall()
    conn.close()
    return result

def delete_subscription(sub_id):
    conn = get_db()
    c = conn.cursor()
    c.execute('DELETE FROM subscriptions WHERE id = ?', (sub_id,))
    conn.commit()
    conn.close()

# ===== توابع مدیریت ربات‌ها =====
def save_user_bot(user_id, bot_token, bot_code, is_permanent):
    conn = get_db()
    c = conn.cursor()
    expires_at = datetime.now() + timedelta(days=1) if is_permanent else None
    c.execute('''
        INSERT INTO user_bots (user_id, bot_token, bot_code, is_permanent, created_at, expires_at)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (user_id, bot_token, bot_code, is_permanent, datetime.now().isoformat(),
          expires_at.isoformat() if expires_at else None))
    conn.commit()
    conn.close()

def get_user_bots(user_id):
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM user_bots WHERE user_id = ? ORDER BY created_at DESC', (user_id,))
    result = c.fetchall()
    conn.close()
    return result

def delete_user_bot(bot_id):
    conn = get_db()
    c = conn.cursor()
    c.execute('DELETE FROM user_bots WHERE id = ?', (bot_id,))
    conn.commit()
    conn.close()

def extract_token_from_code(code):
    patterns = [
        r'TOKEN\\s*=\\s*["\\']([^"\\']+)["\\']',
        r'token\\s*=\\s*["\\']([^"\\']+)["\\']',
        r'BOT_TOKEN\\s*=\\s*["\\']([^"\\']+)["\\']',
        r'API_TOKEN\\s*=\\s*["\\']([^"\\']+)["\\']',
    ]
    for pattern in patterns:
        match = re.search(pattern, code)
        if match:
            return match.group(1)
    return None

def check_token_valid(token):
    try:
        import requests
        url = f"https://api.telegram.org/bot{{token}}/getMe"
        resp = requests.get(url, timeout=5)
        return resp.status_code == 200
    except:
        return False

def run_user_bot(code, token, user_id, is_permanent):
    try:
        code_with_token = inject_token_to_code(code, token)
        temp_dir = tempfile.mkdtemp()
        temp_path = os.path.join(temp_dir, "user_bot.py")
        with open(temp_path, 'w', encoding='utf-8') as f:
            f.write(code_with_token)
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["TOKEN"] = token
        process = subprocess.Popen(
            [sys.executable, temp_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            cwd=temp_dir
        )
        save_user_bot(user_id, token, code, is_permanent)
        return True, "ربات با موفقیت اجرا شد!"
    except Exception as e:
        return False, f"خطا در اجرا: {{e}}"

def inject_token_to_code(code, token):
    token_vars = ['TOKEN', 'token', 'YOUR_TOKEN', 'your_token', 'BOT_TOKEN', 'bot_token', 'API_TOKEN', 'api_token']
    modified_code = code
    for var in token_vars:
        modified_code = re.sub(rf'{{var}}\\s*=\\s*["\\']([^"\\']*)["\\']', f'{{var}} = "{{token}}"', modified_code)
        modified_code = re.sub(rf'{{var}}\\s*=\\s*\\'([^\\']*)\\'', f'{{var}} = "{{token}}"', modified_code)
    if 'TOKEN' not in modified_code and 'token' not in modified_code:
        modified_code = f'TOKEN = "{{token}}"\\n\\n' + modified_code
    return modified_code

# ===== کیبوردهای ربات =====
def main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👤 مدیریت کاربران", callback_data="users")],
        [InlineKeyboardButton("📦 مدیریت اشتراک‌ها", callback_data="subscriptions")],
        [InlineKeyboardButton("🤖 مدیریت ربات‌ها", callback_data="bots")],
        [InlineKeyboardButton("💰 مدیریت مالی", callback_data="finance")],
        [InlineKeyboardButton("📊 گزارشات", callback_data="reports")],
        [InlineKeyboardButton("⚙️ تنظیمات", callback_data="settings")],
        [InlineKeyboardButton("📞 پشتیبانی", callback_data="support")],
        [InlineKeyboardButton("ℹ️ راهنما", callback_data="help")],
    ])

def users_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ ایجاد کاربر جدید", callback_data="add_user")],
        [InlineKeyboardButton("📋 لیست کاربران", callback_data="list_users")],
        [InlineKeyboardButton("✏️ ویرایش کاربر", callback_data="edit_user")],
        [InlineKeyboardButton("🗑️ حذف کاربر", callback_data="delete_user")],
        [InlineKeyboardButton("🔍 جستجوی کاربر", callback_data="search_user")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="back_main")],
    ])

def subscriptions_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ ایجاد اشتراک جدید", callback_data="add_subscription")],
        [InlineKeyboardButton("📋 لیست اشتراک‌ها", callback_data="list_subscriptions")],
        [InlineKeyboardButton("🔄 تمدید اشتراک", callback_data="renew_subscription")],
        [InlineKeyboardButton("🗑️ حذف اشتراک", callback_data="delete_subscription")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="back_main")],
    ])

def bots_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 دریافت فایل کد", callback_data="get_bot_file")],
        [InlineKeyboardButton("📋 لیست ربات‌ها", callback_data="list_bots")],
        [InlineKeyboardButton("🔄 تمدید ربات", callback_data="renew_bot")],
        [InlineKeyboardButton("🗑️ حذف ربات", callback_data="delete_bot")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="back_main")],
    ])

# ===== هندلرهای ربات =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if str(user.id) != str(ADMIN_ID):
        await update.message.reply_text("❌ شما دسترسی به این ربات ندارید!")
        return
    
    await update.message.reply_text(
        "🤖 **ربات مدیریت پنل**\n\n"
        "سلام ادمین عزیز!\n"
        "لطفاً یکی از گزینه‌های زیر را انتخاب کنید:",
        reply_markup=main_keyboard()
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user = update.effective_user
    if str(user.id) != str(ADMIN_ID):
        await query.edit_message_text("❌ شما دسترسی ندارید!")
        return
    
    data = query.data
    
    # ===== منوی اصلی =====
    if data == "back_main":
        await query.edit_message_text(
            "🤖 **ربات مدیریت پنل**\n\n"
            "لطفاً یکی از گزینه‌های زیر را انتخاب کنید:",
            reply_markup=main_keyboard()
        )
        return
    
    # ===== مدیریت کاربران =====
    elif data == "users":
        await query.edit_message_text(
            "👤 **مدیریت کاربران**\n\n"
            "لطفاً یکی از گزینه‌های زیر را انتخاب کنید:",
            reply_markup=users_keyboard()
        )
    
    elif data == "add_user":
        await query.edit_message_text(
            "➕ **ایجاد کاربر جدید**\n\n"
            "لطفاً نام کاربری را وارد کنید:"
        )
        context.user_data['action'] = 'add_user'
    
    elif data == "list_users":
        users = get_all_users()
        if users:
            text = "📋 **لیست کاربران:**\n\n"
            for u in users:
                text += f"🔹 {u['username']} (ID: {u['id']})\n"
                text += f"   └─ موجودی: {u['balance']:,} تومان\n"
                text += f"   └─ وضعیت: {'✅ فعال' if u['is_active'] else '❌ غیرفعال'}\n\n"
            await query.edit_message_text(text)
        else:
            await query.edit_message_text("📋 هیچ کاربری ثبت نشده است.")
    
    elif data == "delete_user":
        await query.edit_message_text(
            "🗑️ **حذف کاربر**\n\n"
            "لطفاً آیدی عددی کاربر را وارد کنید:"
        )
        context.user_data['action'] = 'delete_user'
    
    # ===== مدیریت اشتراک‌ها =====
    elif data == "subscriptions":
        await query.edit_message_text(
            "📦 **مدیریت اشتراک‌ها**\n\n"
            "لطفاً یکی از گزینه‌های زیر را انتخاب کنید:",
            reply_markup=subscriptions_keyboard()
        )
    
    elif data == "add_subscription":
        await query.edit_message_text(
            "➕ **ایجاد اشتراک جدید**\n\n"
            "مراحل:\n"
            "۱. نام کاربری را وارد کنید\n"
            "۲. محدودیت زمان (مثال: ۲ روز، ۳ ساعت)\n"
            "۳. محدودیت حافظه به گیگابایت (مثال: ۱، ۰.۵)"
        )
        context.user_data['action'] = 'add_subscription'
        context.user_data['step'] = 'username'
    
    elif data == "list_subscriptions":
        conn = get_db()
        c = conn.cursor()
        c.execute('SELECT * FROM subscriptions ORDER BY created_at DESC LIMIT 20')
        subs = c.fetchall()
        conn.close()
        
        if subs:
            text = "📋 **لیست اشتراک‌ها:**\n\n"
            for s in subs:
                status = "✅ فعال" if s['is_active'] else "❌ غیرفعال"
                text += f"🔹 {s['username']}\n"
                text += f"   ├─ ⏰ {s['time_limit']}\n"
                text += f"   ├─ 💾 {s['storage_limit']} GB\n"
                text += f"   ├─ 🔗 {s['link_token'][:10]}...\n"
                text += f"   ├─ 🔑 {s['access_code']}\n"
                text += f"   └─ وضعیت: {status}\n\n"
            await query.edit_message_text(text)
        else:
            await query.edit_message_text("📋 هیچ اشتراکی ثبت نشده است.")
    
    elif data == "renew_subscription":
        await query.edit_message_text(
            "🔄 **تمدید اشتراک**\n\n"
            "لطفاً آیدی اشتراک و زمان جدید را وارد کنید:\n"
            "مثال: 5 2 روز"
        )
        context.user_data['action'] = 'renew_subscription'
    
    elif data == "delete_subscription":
        await query.edit_message_text(
            "🗑️ **حذف اشتراک**\n\n"
            "لطفاً آیدی اشتراک را وارد کنید:"
        )
        context.user_data['action'] = 'delete_subscription'
    
    # ===== مدیریت ربات‌ها =====
    elif data == "bots":
        await query.edit_message_text(
            "🤖 **مدیریت ربات‌ها**\n\n"
            "لطفاً یکی از گزینه‌های زیر را انتخاب کنید:",
            reply_markup=bots_keyboard()
        )
    
    elif data == "get_bot_file":
        await query.edit_message_text(
            "📤 **دریافت فایل کد ربات**\n\n"
            "لطفاً فایل کد ربات را ارسال کنید.\n"
            "من توکن را استخراج و ربات را اجرا می‌کنم."
        )
        context.user_data['action'] = 'get_bot_file'
    
    elif data == "list_bots":
        await query.edit_message_text("📋 **لیست ربات‌ها**\n\nدر حال توسعه...")
    
    elif data == "renew_bot":
        await query.edit_message_text(
            "🔄 **تمدید ربات**\n\n"
            "لطفاً توکن ربات را وارد کنید:"
        )
        context.user_data['action'] = 'renew_bot'
    
    elif data == "delete_bot":
        await query.edit_message_text(
            "🗑️ **حذف ربات**\n\n"
            "لطفاً توکن ربات را وارد کنید:"
        )
        context.user_data['action'] = 'delete_bot'
    
    # ===== راهنما =====
    elif data == "help":
        await query.edit_message_text(
            "ℹ️ **راهنما**\n\n"
            "👤 **مدیریت کاربران:**\n"
            "• ایجاد، ویرایش، حذف کاربران\n\n"
            "📦 **مدیریت اشتراک‌ها:**\n"
            "• ایجاد اشتراک با زمان و حافظه محدود\n"
            "• تمدید و حذف اشتراک\n\n"
            "🤖 **مدیریت ربات‌ها:**\n"
            "• دریافت فایل کد و اجرای خودکار\n"
            "• تمدید و حذف ربات\n\n"
            "💰 **مدیریت مالی:**\n"
            "• مدیریت کیف پول و رسیدها"
        )

# ===== هندلر پیام‌ها =====
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if str(user.id) != str(ADMIN_ID):
        return
    
    text = update.message.text
    action = context.user_data.get('action')
    
    if action == 'add_user':
        # ایجاد کاربر جدید
        user_id = create_user(text)
        if user_id:
            await update.message.reply_text(
                f"✅ **کاربر با موفقیت ساخته شد!**\n\n"
                f"👤 نام کاربری: {text}\n"
                f"🆔 آیدی: {user_id}\n\n"
                f"اکنون می‌توانید برای این کاربر اشتراک بسازید."
            )
            context.user_data['action'] = None
        else:
            await update.message.reply_text("❌ این نام کاربری قبلاً ثبت شده است!")
    
    elif action == 'delete_user':
        try:
            user_id = int(text)
            delete_user(user_id)
            await update.message.reply_text(f"✅ کاربر با آیدی {user_id} حذف شد!")
            context.user_data['action'] = None
        except:
            await update.message.reply_text("❌ لطفاً یک آیدی عددی معتبر وارد کنید!")
    
    elif action == 'add_subscription':
        step = context.user_data.get('step', 'username')
        
        if step == 'username':
            context.user_data['sub_username'] = text
            context.user_data['step'] = 'time'
            await update.message.reply_text(
                "⏰ **محدودیت زمان را وارد کنید:**\n\n"
                "مثال‌ها:\n"
                "• ۱ ساعت\n"
                "• ۲ روز\n"
                "• ۳ هفته\n"
                "• ۱ ماه"
            )
        
        elif step == 'time':
            context.user_data['sub_time'] = text
            context.user_data['step'] = 'storage'
            await update.message.reply_text(
                "💾 **محدودیت حافظه را وارد کنید (به گیگابایت):**\n\n"
                "مثال‌ها:\n"
                "• ۱ (یعنی ۱ گیگابایت)\n"
                "• ۰.۵ (یعنی ۵۰۰ مگابایت)\n"
                "• ۲ (یعنی ۲ گیگابایت)"
            )
        
        elif step == 'storage':
            try:
                storage = float(text)
                username = context.user_data.get('sub_username')
                time_str = context.user_data.get('sub_time')
                
                # پیدا کردن کاربر
                conn = get_db()
                c = conn.cursor()
                c.execute('SELECT id FROM users WHERE username = ?', (username,))
                user_row = c.fetchone()
                conn.close()
                
                if not user_row:
                    await update.message.reply_text("❌ کاربری با این نام پیدا نشد!")
                    context.user_data['action'] = None
                    context.user_data['step'] = None
                    return
                
                user_id = user_row[0]
                
                # ایجاد اشتراک
                link_token, access_code = create_subscription(user_id, username, storage, time_str)
                
                await update.message.reply_text(
                    f"✅ **اشتراک با موفقیت ساخته شد!**\n\n"
                    f"👤 کاربر: {username}\n"
                    f"⏰ زمان: {time_str}\n"
                    f"💾 حافظه: {storage} GB\n"
                    f"🔗 لینک: https://yourdomain.com/user/{link_token}\n"
                    f"🔑 رمز: {access_code}\n\n"
                    f"💡 این لینک و رمز را به کاربر ارسال کنید."
                )
                
                context.user_data['action'] = None
                context.user_data['step'] = None
                
            except ValueError:
                await update.message.reply_text("❌ لطفاً یک عدد معتبر وارد کنید!")
    
    elif action == 'renew_subscription':
        try:
            parts = text.split(' ', 1)
            sub_id = int(parts[0])
            new_time = parts[1]
            
            time_seconds = parse_time_limit(new_time)
            if not time_seconds:
                await update.message.reply_text("❌ فرمت زمان نامعتبر است!")
                return
            
            conn = get_db()
            c = conn.cursor()
            expires_at = datetime.now() + timedelta(seconds=time_seconds)
            c.execute('''
                UPDATE subscriptions 
                SET expires_at = ?, time_limit = ?, time_limit_seconds = ?, is_active = 1
                WHERE id = ?
            ''', (expires_at.isoformat(), new_time, time_seconds, sub_id))
            conn.commit()
            conn.close()
            
            await update.message.reply_text(f"✅ اشتراک {sub_id} تا {new_time} دیگر تمدید شد!")
            context.user_data['action'] = None
            
        except:
            await update.message.reply_text("❌ لطفاً آیدی و زمان را به درستی وارد کنید!")
    
    elif action == 'delete_subscription':
        try:
            sub_id = int(text)
            delete_subscription(sub_id)
            await update.message.reply_text(f"✅ اشتراک {sub_id} حذف شد!")
            context.user_data['action'] = None
        except:
            await update.message.reply_text("❌ لطفاً یک آیدی معتبر وارد کنید!")
    
    elif action == 'get_bot_file':
        # اینجا هندلر فایل جداگانه است
        pass

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if str(user.id) != str(ADMIN_ID):
        return
    
    if context.user_data.get('action') != 'get_bot_file':
        return
    
    document = update.message.document
    if not document:
        await update.message.reply_text("❌ لطفاً یک فایل ارسال کنید.")
        return
    
    try:
        file = await document.get_file()
        file_content = await file.download_as_bytearray()
        code = file_content.decode('utf-8', errors='ignore')
        
        token = extract_token_from_code(code)
        
        if token:
            if check_token_valid(token):
                await update.message.reply_text(
                    f"✅ **توکن پیدا شد و معتبر است!**\n\n"
                    f"🔑 توکن: `{token[:10]}...`\n\n"
                    f"ربات در حال اجرا است..."
                )
                # اجرای ربات
                success, msg = run_user_bot(code, token, 1, False)
                await update.message.reply_text(
                    f"✅ ربات با موفقیت اجرا شد!" if success else f"❌ {msg}"
                )
            else:
                await update.message.reply_text(
                    f"⚠️ **توکن پیدا شد اما نامعتبر است!**\n\n"
                    f"توکن: `{token[:10]}...`\n\n"
                    f"لطفاً توکن صحیح را وارد کنید:"
                )
                context.user_data['code_without_token'] = code
                context.user_data['action'] = 'enter_token'
        else:
            await update.message.reply_text(
                "⚠️ **توکن در کد پیدا نشد!**\n\n"
                "لطفاً توکن ربات خود را وارد کنید:"
            )
            context.user_data['code_without_token'] = code
            context.user_data['action'] = 'enter_token'
            
    except Exception as e:
        await update.message.reply_text(f"❌ خطا در پردازش فایل: {e}")

async def handle_token_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if str(user.id) != str(ADMIN_ID):
        return
    
    if context.user_data.get('action') != 'enter_token':
        return
    
    token = update.message.text.strip()
    code = context.user_data.get('code_without_token')
    
    if not code:
        await update.message.reply_text("❌ کدی برای ذخیره وجود ندارد. لطفاً دوباره فایل را ارسال کنید.")
        return
    
    if check_token_valid(token):
        await update.message.reply_text(
            f"✅ **توکن معتبر است!**\n\n"
            f"🔑 توکن: `{token[:10]}...`\n\n"
            f"ربات در حال اجرا است..."
        )
        success, msg = run_user_bot(code, token, 1, False)
        await update.message.reply_text(
            f"✅ ربات با موفقیت اجرا شد!" if success else f"❌ {msg}"
        )
        context.user_data['action'] = None
    else:
        await update.message.reply_text("❌ توکن نامعتبر است! لطفاً دوباره تلاش کنید.")

# ===== راه‌اندازی ربات =====
def run_admin_bot():
    while True:
        try:
            app_bot = Application.builder().token(TOKEN).build()
            app_bot.add_handler(CommandHandler("start", start))
            app_bot.add_handler(CallbackQueryHandler(button_handler))
            app_bot.add_handler(MessageHandler(filters.Document.ALL, handle_document))
            app_bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
            logger.info("🚀 ربات ادمین شروع به کار کرد...")
            app_bot.run_polling()
        except Exception as e:
            logger.error(f"❌ خطا در ربات ادمین: {e}")
            time.sleep(5)
'''

# ============================================================
# ===== مسیرهای سایت =====
# ============================================================

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('admin_logged_in'):
            flash('❌ لطفاً ابتدا وارد شوید!', 'error')
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if username == 'admin' and password == 'Admin@123':
            session['admin_logged_in'] = True
            flash('✅ ورود موفق!', 'success')
            return redirect(url_for('admin_dashboard'))
        else:
            flash('❌ نام کاربری یا رمز عبور اشتباه است!', 'error')
    return render_template('admin/login.html')

@app.route('/admin/logout')
def admin_logout():
    session.clear()
    flash('✅ خروج با موفقیت انجام شد!', 'success')
    return redirect(url_for('admin_login'))

@app.route('/admin')
@login_required
def admin_dashboard():
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM users')
    total_users = c.fetchone()[0]
    c.execute('SELECT COUNT(*) FROM subscriptions WHERE is_active = 1')
    active_subs = c.fetchone()[0]
    c.execute('SELECT COUNT(*) FROM user_bots')
    total_bots = c.fetchone()[0]
    conn.close()
    return render_template('admin/dashboard.html', 
                          total_users=total_users,
                          active_subs=active_subs,
                          total_bots=total_bots)

@app.route('/admin/users')
@login_required
def admin_users():
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM users ORDER BY created_at DESC')
    users = c.fetchall()
    conn.close()
    return render_template('admin/users.html', users=users)

@app.route('/admin/subscriptions')
@login_required
def admin_subscriptions():
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM subscriptions ORDER BY created_at DESC')
    subs = c.fetchall()
    conn.close()
    return render_template('admin/subscriptions.html', subscriptions=subs)

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        try:
            token = request.form.get('token', '').strip()
            code_type = request.form.get('code_type', 'file')
            is_permanent = request.form.get('is_permanent') == 'on'
            
            if not token:
                return render_template('result.html', 
                    result={'success': False, 'message': '❌ لطفاً توکن را وارد کنید', 'logs': ''})
            
            code_content = ""
            
            if code_type == 'file':
                if 'code_file' in request.files:
                    file = request.files['code_file']
                    if file.filename != '':
                        file_content = file.read()
                        if len(file_content) > MAX_CODE_SIZE:
                            return render_template('result.html', 
                                result={'success': False, 'message': f'❌ حجم فایل بیشتر از {MAX_CODE_SIZE//1024} کیلوبایت است', 'logs': ''})
                        code_content = file_content.decode('utf-8', errors='ignore')
                
                if not code_content:
                    code_content = request.form.get('code_text', '').strip()
                    if not code_content:
                        return render_template('result.html', 
                            result={'success': False, 'message': '❌ لطفاً یک فایل انتخاب کنید یا کد را وارد کنید', 'logs': ''})
            else:
                code_content = request.form.get('code_text', '').strip()
                if not code_content:
                    return render_template('result.html', 
                        result={'success': False, 'message': '❌ لطفاً کد را وارد کنید', 'logs': ''})
            
            # چک کردن توکن
            try:
                url = f"https://api.telegram.org/bot{token}/getMe"
                resp = requests.get(url, timeout=5)
                if resp.status_code != 200:
                    return render_template('result.html', 
                        result={'success': False, 'message': '❌ توکن تلگرام نامعتبر است!', 
                               'logs': 'لطفاً توکن صحیح را از @BotFather دریافت کنید'})
            except:
                return render_template('result.html', 
                    result={'success': False, 'message': '❌ خطا در بررسی توکن!', 
                           'logs': 'لطفاً دوباره تلاش کنید'})
            
            # اجرای ربات
            result = execute_user_bot(code_content, token, 1, is_permanent)
            return render_template('result.html', result=result)
            
        except Exception as e:
            return render_template('result.html', 
                result={'success': False, 'message': f'❌ خطای سیستمی: {str(e)}', 
                       'logs': traceback.format_exc()})
    
    return render_template('index.html')

@app.route('/user/<link_token>', methods=['GET', 'POST'])
def user_panel(link_token):
    """پنل کاربری با لینک اختصاصی"""
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM subscriptions WHERE link_token = ? AND is_active = 1', (link_token,))
    subscription = c.fetchone()
    conn.close()
    
    if not subscription:
        return render_template('error.html', message='❌ لینک نامعتبر یا منقضی شده است!')
    
    if request.method == 'POST':
        access_code = request.form.get('access_code')
        if access_code == subscription['access_code']:
            session['user_id'] = subscription['user_id']
            session['username'] = subscription['username']
            flash('✅ ورود موفق!', 'success')
            return redirect(url_for('user_dashboard'))
        else:
            flash('❌ رمز عبور اشتباه است!', 'error')
    
    return render_template('user/login.html', subscription=subscription)

@app.route('/user/dashboard')
def user_dashboard():
    if not session.get('user_id'):
        return redirect(url_for('index'))
    
    user_id = session.get('user_id')
    subscription = check_subscription_valid(user_id)
    
    if not subscription:
        flash('❌ اشتراک شما منقضی شده است!', 'error')
        return redirect(url_for('index'))
    
    return render_template('user/dashboard.html', subscription=subscription)

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'online', 'python': sys.version, 'timestamp': time.time()})

# ============================================================
# ===== اجرا =====
# ============================================================

if __name__ == '__main__':
    # اجرای ربات ادمین در پس‌زمینه
    admin_bot_thread = threading.Thread(target=lambda: exec(compile(ADMIN_BOT_CODE, '<string>', 'exec')), daemon=True)
    # بهتر است ربات را به صورت جداگانه اجرا کنید
    
    port = int(os.environ.get('PORT', 8080))
    print("\n" + "="*60)
    print("🤖 سیستم مدیریت ربات‌ها (سایت + ربات ادمین)")
    print("="*60)
    print(f"📡 پورت: {port}")
    print("🌐 آدرس: http://localhost:" + str(port))
    print("👤 ادمین: admin / Admin@123")
    print("💡 تیک 'همیشه روشن' = ربات ۲۴ ساعته روشن میمونه")
    print("="*60 + "\n")
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
