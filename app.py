#!/usr/bin/env python3
# -*- coding: utf-8 -*-

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
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
import subprocess
import tempfile

# ============================================================
# ===== تنظیمات =====
# ============================================================

TOKEN = "8262116870:AAGhf7siH7qpVm4nPAM8kGJcPwgyu0PZZFo"
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
# ===== توابع دیتابیس =====
# ============================================================

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
    """تبدیل متن زمان به ثانیه"""
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
    
    for unit, seconds in units.items():
        if unit in time_str:
            number = re.search(r'(\d+\.?\d*)', time_str)
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
    except sqlite3.IntegrityError:
        return None
    finally:
        conn.close()

def create_subscription(user_id, username, storage_limit, time_limit):
    conn = get_db()
    c = conn.cursor()
    
    link_token = generate_link_token()
    access_code = generate_access_code()
    
    # محاسبه زمان انقضا
    seconds = parse_time_limit(time_limit)
    if seconds:
        expires_at = datetime.now() + timedelta(seconds=seconds)
    else:
        expires_at = datetime.now() + timedelta(days=1)
    
    c.execute('''
        INSERT INTO subscriptions (user_id, username, link_token, access_code, storage_limit, time_limit, expires_at, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (user_id, username, link_token, access_code, storage_limit, time_limit, expires_at.isoformat(), datetime.now().isoformat()))
    
    sub_id = c.lastrowid
    conn.commit()
    conn.close()
    
    return {
        'id': sub_id,
        'link_token': link_token,
        'access_code': access_code,
        'storage_limit': storage_limit,
        'time_limit': time_limit,
        'expires_at': expires_at
    }

def get_user_by_username(username):
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM users WHERE username = ?', (username,))
    result = c.fetchone()
    conn.close()
    return result

def get_subscription_by_token(token):
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM subscriptions WHERE link_token = ?', (token,))
    result = c.fetchone()
    conn.close()
    return result

def get_all_subscriptions():
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM subscriptions ORDER BY created_at DESC')
    result = c.fetchall()
    conn.close()
    return result

def get_all_users():
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM users ORDER BY created_at DESC')
    result = c.fetchall()
    conn.close()
    return result

def delete_subscription(sub_id):
    conn = get_db()
    c = conn.cursor()
    c.execute('DELETE FROM subscriptions WHERE id = ?', (sub_id,))
    conn.commit()
    conn.close()
    return True

def update_subscription_time(sub_id, new_time_limit):
    conn = get_db()
    c = conn.cursor()
    seconds = parse_time_limit(new_time_limit)
    if seconds:
        expires_at = datetime.now() + timedelta(seconds=seconds)
    else:
        expires_at = datetime.now() + timedelta(days=1)
    
    c.execute('UPDATE subscriptions SET time_limit = ?, expires_at = ? WHERE id = ?',
              (new_time_limit, expires_at.isoformat(), sub_id))
    conn.commit()
    conn.close()
    return True

# ============================================================
# ===== کیبوردها =====
# ============================================================

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

# ============================================================
# ===== دستورات ربات =====
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if str(user.id) != str(ADMIN_ID):
        await update.message.reply_text("❌ شما دسترسی به این ربات ندارید!")
        return
    
    await update.message.reply_text(
        "🤖 **ربات مدیریت پنل**\n\n"
        "سلام ادمین عزیز! به پنل مدیریت خوش آمدید.\n"
        "لطفاً یکی از گزینه‌های زیر را انتخاب کنید:",
        reply_markup=main_menu_keyboard()
    )

# ============================================================
# ===== مدیریت کاربران =====
# ============================================================

async def add_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "👤 **ایجاد کاربر جدید**\n\n"
        "لطفاً نام کاربری را وارد کنید:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="users_menu")]])
    )
    context.user_data['action'] = 'add_user_username'

async def handle_add_user_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = update.message.text.strip()
    context.user_data['new_username'] = username
    context.user_data['action'] = 'add_user_time'
    await update.message.reply_text(
        f"👤 نام کاربری: {username}\n\n"
        "⏰ **محدودیت زمان** را وارد کنید:\n"
        "مثال: ۱ساعت، ۲روز، ۳ماه، ۱سال",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="users_menu")]])
    )

async def handle_add_user_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    time_limit = update.message.text.strip()
    context.user_data['new_time'] = time_limit
    context.user_data['action'] = 'add_user_storage'
    await update.message.reply_text(
        f"⏰ زمان: {time_limit}\n\n"
        "💾 **محدودیت حافظه** را به گیگابایت وارد کنید:\n"
        "مثال: ۱ (یعنی ۱ گیگابایت) یا ۰.۵ (یعنی ۵۰۰ مگابایت)",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="users_menu")]])
    )

async def handle_add_user_storage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        storage = float(update.message.text.strip().replace(',', '.'))
    except:
        await update.message.reply_text("❌ عدد معتبر وارد کنید!")
        return
    
    username = context.user_data.get('new_username')
    time_limit = context.user_data.get('new_time')
    
    # ایجاد کاربر
    user_id = create_user(username)
    if not user_id:
        await update.message.reply_text("❌ این نام کاربری قبلاً ثبت شده است!")
        context.user_data['action'] = None
        return
    
    # ایجاد اشتراک
    sub = create_subscription(user_id, username, storage, time_limit)
    
    await update.message.reply_text(
        f"✅ **کاربر با موفقیت ساخته شد!**\n\n"
        f"👤 نام کاربری: {username}\n"
        f"⏰ زمان: {time_limit}\n"
        f"💾 حافظه: {storage} گیگابایت\n\n"
        f"🔗 **لینک:** https://yourdomain.com/user/{sub['link_token']}\n"
        f"🔑 **رمز:** `{sub['access_code']}`\n\n"
        f"💡 این اطلاعات را به کاربر ارسال کنید.",
        parse_mode='Markdown'
    )
    context.user_data['action'] = None

async def list_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    users = get_all_users()
    if not users:
        await query.edit_message_text("📋 هیچ کاربری ثبت نشده است.")
        return
    
    text = "📋 **لیست کاربران:**\n\n"
    for user in users[:20]:
        text += f"👤 {user['username']} | {'✅ فعال' if user['is_active'] else '❌ غیرفعال'}\n"
        text += f"   📅 {user['created_at'][:10]}\n\n"
    
    if len(users) > 20:
        text += f"... و {len(users) - 20} کاربر دیگر"
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="users_menu")]]))

# ============================================================
# ===== مدیریت اشتراک‌ها =====
# ============================================================

async def add_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "📦 **ایجاد اشتراک جدید**\n\n"
        "لطفاً نام کاربری را وارد کنید:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="subs_menu")]])
    )
    context.user_data['action'] = 'add_sub_username'

async def handle_add_sub_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = update.message.text.strip()
    user = get_user_by_username(username)
    if not user:
        await update.message.reply_text("❌ کاربری با این نام پیدا نشد!")
        return
    
    context.user_data['sub_username'] = username
    context.user_data['sub_user_id'] = user['id']
    context.user_data['action'] = 'add_sub_time'
    await update.message.reply_text(
        f"👤 کاربر: {username}\n\n"
        "⏰ **محدودیت زمان** را وارد کنید:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="subs_menu")]])
    )

async def handle_add_sub_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    time_limit = update.message.text.strip()
    context.user_data['sub_time'] = time_limit
    context.user_data['action'] = 'add_sub_storage'
    await update.message.reply_text(
        f"⏰ زمان: {time_limit}\n\n"
        "💾 **محدودیت حافظه** را به گیگابایت وارد کنید:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="subs_menu")]])
    )

async def handle_add_sub_storage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        storage = float(update.message.text.strip().replace(',', '.'))
    except:
        await update.message.reply_text("❌ عدد معتبر وارد کنید!")
        return
    
    username = context.user_data.get('sub_username')
    user_id = context.user_data.get('sub_user_id')
    time_limit = context.user_data.get('sub_time')
    
    sub = create_subscription(user_id, username, storage, time_limit)
    
    await update.message.reply_text(
        f"✅ **اشتراک با موفقیت ساخته شد!**\n\n"
        f"👤 کاربر: {username}\n"
        f"⏰ زمان: {time_limit}\n"
        f"💾 حافظه: {storage} گیگابایت\n\n"
        f"🔗 **لینک:** https://yourdomain.com/user/{sub['link_token']}\n"
        f"🔑 **رمز:** `{sub['access_code']}`\n\n"
        f"💡 این اطلاعات را به کاربر ارسال کنید.",
        parse_mode='Markdown'
    )
    context.user_data['action'] = None

async def list_subscriptions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    subs = get_all_subscriptions()
    if not subs:
        await query.edit_message_text("📋 هیچ اشتراکی ثبت نشده است.")
        return
    
    text = "📋 **لیست اشتراک‌ها:**\n\n"
    for sub in subs[:10]:
        text += f"👤 {sub['username']}\n"
        text += f"💾 {sub['storage_limit']} GB | ⏰ {sub['time_limit']}\n"
        text += f"🔗 `{sub['link_token'][:10]}...`\n"
        text += f"📅 {sub['created_at'][:10]} | {'✅ فعال' if sub['is_active'] else '❌ غیرفعال'}\n\n"
    
    await query.edit_message_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="subs_menu")]]))

# ============================================================
# ===== مدیریت ربات‌ها =====
# ============================================================

async def get_bot_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "📤 **دریافت فایل کد ربات**\n\n"
        "لطفاً فایل .py یا .txt حاوی کد ربات را ارسال کنید.\n"
        "من به‌طور خودکار توکن رو از کد استخراج میکنم.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="bots_menu")]])
    )
    context.user_data['waiting_for_bot_file'] = True

async def handle_bot_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get('waiting_for_bot_file'):
        return
    
    document = update.message.document
    if not document:
        await update.message.reply_text("❌ لطفاً یک فایل ارسال کنید.")
        return
    
    # دانلود فایل
    file = await document.get_file()
    file_content = await file.download_as_bytearray()
    code = file_content.decode('utf-8', errors='ignore')
    
    # استخراج توکن
    token = extract_token_from_code(code)
    
    if token:
        await update.message.reply_text(
            f"✅ توکن پیدا شد: `{token[:10]}...`\n\n"
            "لطفاً نام کاربری که این ربات برایش است را وارد کنید:",
            parse_mode='Markdown'
        )
        context.user_data['bot_code'] = code
        context.user_data['bot_token'] = token
        context.user_data['action'] = 'bot_assign_user'
    else:
        await update.message.reply_text(
            "⚠️ **توکن در کد پیدا نشد!**\n\n"
            "لطفاً توکن ربات خود را وارد کنید:"
        )
        context.user_data['bot_code_no_token'] = code
        context.user_data['action'] = 'bot_enter_token'

def extract_token_from_code(code):
    patterns = [
        r'TOKEN\s*=\s*["\']([^"\']+)["\']',
        r'token\s*=\s*["\']([^"\']+)["\']',
        r'BOT_TOKEN\s*=\s*["\']([^"\']+)["\']',
        r'API_TOKEN\s*=\s*["\']([^"\']+)["\']',
    ]
    for pattern in patterns:
        match = re.search(pattern, code)
        if match:
            return match.group(1)
    return None

def check_token_valid(token):
    try:
        url = f"https://api.telegram.org/bot{token}/getMe"
        resp = requests.get(url, timeout=5)
        return resp.status_code == 200
    except:
        return False

def run_user_bot(code, token):
    try:
        temp_dir = tempfile.mkdtemp()
        temp_path = os.path.join(temp_dir, "user_bot.py")
        
        # تزریق توکن
        code_with_token = re.sub(r'TOKEN\s*=\s*["\'][^"\']*["\']', f'TOKEN = "{token}"', code)
        if code_with_token == code:
            code_with_token = f'TOKEN = "{token}"\n\n' + code
        
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
        
        return True, "ربات با موفقیت اجرا شد!"
    except Exception as e:
        return False, f"خطا در اجرا: {e}"

# ============================================================
# ===== مدیریت دکمه‌ها (Callback Handler) =====
# ============================================================

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user = update.effective_user
    if str(user.id) != str(ADMIN_ID):
        await query.edit_message_text("❌ شما دسترسی ندارید!")
        return
    
    data = query.data
    
    # منوهای اصلی
    if data == "users_menu":
        await query.edit_message_text("👤 **مدیریت کاربران**\n\nلطفاً یکی از گزینه‌ها را انتخاب کنید:", reply_markup=users_menu_keyboard())
    elif data == "subs_menu":
        await query.edit_message_text("📦 **مدیریت اشتراک‌ها**\n\nلطفاً یکی از گزینه‌ها را انتخاب کنید:", reply_markup=subs_menu_keyboard())
    elif data == "bots_menu":
        await query.edit_message_text("🤖 **مدیریت ربات‌ها**\n\nلطفاً یکی از گزینه‌ها را انتخاب کنید:", reply_markup=bots_menu_keyboard())
    elif data == "finance_menu":
        await query.edit_message_text("💰 **مدیریت مالی**\n\nلطفاً یکی از گزینه‌ها را انتخاب کنید:", reply_markup=finance_menu_keyboard())
    elif data == "reports_menu":
        await query.edit_message_text("📊 **گزارشات**\n\nلطفاً یکی از گزینه‌ها را انتخاب کنید:", reply_markup=reports_menu_keyboard())
    elif data == "settings_menu":
        await query.edit_message_text("⚙️ **تنظیمات**\n\nلطفاً یکی از گزینه‌ها را انتخاب کنید:", reply_markup=settings_menu_keyboard())
    elif data == "support_menu":
        await query.edit_message_text("📞 **پشتیبانی**\n\nلطفاً یکی از گزینه‌ها را انتخاب کنید:", reply_markup=support_menu_keyboard())
    
    # مدیریت کاربران
    elif data == "add_user":
        await add_user(update, context)
    elif data == "list_users":
        await list_users(update, context)
    elif data == "edit_user":
        await query.edit_message_text("✏️ **ویرایش کاربر**\n\nلطفاً نام کاربری را وارد کنید:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="users_menu")]]))
        context.user_data['action'] = 'edit_user_search'
    elif data == "delete_user":
        await query.edit_message_text("🗑️ **حذف کاربر**\n\nلطفاً نام کاربری را وارد کنید:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="users_menu")]]))
        context.user_data['action'] = 'delete_user_search'
    elif data == "search_user":
        await query.edit_message_text("🔍 **جستجوی کاربر**\n\nلطفاً نام کاربری را وارد کنید:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="users_menu")]]))
        context.user_data['action'] = 'search_user_query'
    
    # مدیریت اشتراک‌ها
    elif data == "add_sub":
        await add_subscription(update, context)
    elif data == "list_subs":
        await list_subscriptions(update, context)
    elif data == "renew_sub":
        await query.edit_message_text("🔄 **تمدید اشتراک**\n\nلطفاً نام کاربری را وارد کنید:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="subs_menu")]]))
        context.user_data['action'] = 'renew_sub_search'
    elif data == "delete_sub":
        await query.edit_message_text("🗑️ **حذف اشتراک**\n\nلطفاً نام کاربری را وارد کنید:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="subs_menu")]]))
        context.user_data['action'] = 'delete_sub_search'
    
    # مدیریت ربات‌ها
    elif data == "get_bot_file":
        await get_bot_file(update, context)
    elif data == "list_bots":
        await query.edit_message_text("📋 **لیست ربات‌های فعال**\n\nدر حال توسعه...", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="bots_menu")]]))
    elif data == "renew_bot":
        await query.edit_message_text("🔄 **تمدید ربات**\n\nلطفاً توکن ربات را وارد کنید:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="bots_menu")]]))
        context.user_data['action'] = 'renew_bot_token'
    elif data == "delete_bot":
        await query.edit_message_text("🗑️ **حذف ربات**\n\nلطفاً توکن ربات را وارد کنید:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="bots_menu")]]))
        context.user_data['action'] = 'delete_bot_token'
    
    # مدیریت مالی
    elif data == "wallets":
        await query.edit_message_text("💰 **کیف پول کاربران**\n\nدر حال توسعه...", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="finance_menu")]]))
    elif data == "add_balance":
        await query.edit_message_text("➕ **افزایش موجودی**\n\nلطفاً نام کاربری و مبلغ را وارد کنید:\nمثال: alirez 50000", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="finance_menu")]]))
        context.user_data['action'] = 'add_balance_input'
    elif data == "transactions":
        await query.edit_message_text("📜 **تاریخچه تراکنش‌ها**\n\nدر حال توسعه...", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="finance_menu")]]))
    elif data == "receipts":
        await query.edit_message_text("📩 **رسیدها**\n\nدر حال توسعه...", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="finance_menu")]]))
    
    # گزارشات
    elif data == "stats":
        users = get_all_users()
        subs = get_all_subscriptions()
        text = f"📊 **آمار کلی**\n\n"
        text += f"👤 کل کاربران: {len(users)}\n"
        text += f"📦 کل اشتراک‌ها: {len(subs)}\n"
        text += f"💰 درآمد کل: در حال توسعه...\n"
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="reports_menu")]]))
    elif data == "activity_report":
        await query.edit_message_text("📈 **گزارش فعالیت**\n\nدر حال توسعه...", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="reports_menu")]]))
    elif data == "usage_report":
        await query.edit_message_text("📉 **گزارش مصرف**\n\nدر حال توسعه...", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="reports_menu")]]))
    
    # تنظیمات
    elif data == "manage_prices":
        await query.edit_message_text("💰 **مدیریت قیمت‌ها**\n\nدر حال توسعه...", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="settings_menu")]]))
    elif data == "system_messages":
        await query.edit_message_text("📝 **پیام‌های سیستمی**\n\nدر حال توسعه...", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="settings_menu")]]))
    
    # پشتیبانی
    elif data == "get_ticket":
        await query.edit_message_text("📩 **دریافت تیکت**\n\nلطفاً موضوع و متن تیکت را وارد کنید:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="support_menu")]]))
        context.user_data['action'] = 'get_ticket_input'
    elif data == "list_tickets":
        await query.edit_message_text("📋 **لیست تیکت‌ها**\n\nدر حال توسعه...", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="support_menu")]]))
    
    # بازگشت
    elif data == "back_main":
        await query.edit_message_text(
            "🤖 **ربات مدیریت پنل**\n\n"
            "سلام ادمین عزیز! لطفاً یکی از گزینه‌ها را انتخاب کنید:",
            reply_markup=main_menu_keyboard()
        )
    
    elif data == "help":
        await query.edit_message_text(
            "ℹ️ **راهنما**\n\n"
            "👤 **مدیریت کاربران:**\n"
            "• ایجاد، ویرایش، حذف و جستجوی کاربران\n\n"
            "📦 **مدیریت اشتراک‌ها:**\n"
            "• ایجاد اشتراک با محدودیت زمان و حافظه\n"
            "• تولید لینک و رمز اختصاصی\n\n"
            "🤖 **مدیریت ربات‌ها:**\n"
            "• دریافت فایل کد و استخراج توکن\n"
            "• اجرا و تمدید ربات‌ها\n\n"
            "💰 **مدیریت مالی:**\n"
            "• کیف پول، رسیدها و تراکنش‌ها\n\n"
            "📊 **گزارشات:**\n"
            "• آمار کلی و گزارش‌های دقیق",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="back_main")]])
        )

# ============================================================
# ===== مدیریت پیام‌های متنی =====
# ============================================================

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if str(user.id) != str(ADMIN_ID):
        return
    
    text = update.message.text.strip()
    action = context.user_data.get('action')
    
    if not action:
        return
    
    # مدیریت اضافه کردن کاربر
    if action == 'add_user_username':
        await handle_add_user_username(update, context)
    elif action == 'add_user_time':
        await handle_add_user_time(update, context)
    elif action == 'add_user_storage':
        await handle_add_user_storage(update, context)
    
    # مدیریت اضافه کردن اشتراک
    elif action == 'add_sub_username':
        await handle_add_sub_username(update, context)
    elif action == 'add_sub_time':
        await handle_add_sub_time(update, context)
    elif action == 'add_sub_storage':
        await handle_add_sub_storage(update, context)
    
    # مدیریت ربات
    elif action == 'bot_assign_user':
        username = text
        user_data = get_user_by_username(username)
        if not user_data:
            await update.message.reply_text("❌ کاربری با این نام پیدا نشد!")
            return
        
        # ذخیره و اجرای ربات
        token = context.user_data.get('bot_token')
        code = context.user_data.get('bot_code')
        
        if check_token_valid(token):
            success, msg = run_user_bot(code, token)
            if success:
                await update.message.reply_text(
                    f"✅ **ربات با موفقیت اجرا شد!**\n\n"
                    f"👤 کاربر: {username}\n"
                    f"🔑 توکن: `{token[:10]}...`\n\n"
                    f"ربات شما روشن است.",
                    parse_mode='Markdown'
                )
            else:
                await update.message.reply_text(f"❌ خطا در اجرا: {msg}")
        else:
            await update.message.reply_text("❌ توکن نامعتبر است!")
        
        context.user_data['action'] = None
    
    elif action == 'bot_enter_token':
        token = text
        code = context.user_data.get('bot_code_no_token')
        
        if check_token_valid(token):
            code_with_token = f'TOKEN = "{token}"\n\n' + code
            success, msg = run_user_bot(code_with_token, token)
            if success:
                await update.message.reply_text(
                    f"✅ **ربات با موفقیت اجرا شد!**\n\n"
                    f"🔑 توکن: `{token[:10]}...`\n\n"
                    f"ربات شما روشن است.",
                    parse_mode='Markdown'
                )
            else:
                await update.message.reply_text(f"❌ خطا در اجرا: {msg}")
        else:
            await update.message.reply_text("❌ توکن نامعتبر است!")
        
        context.user_data['action'] = None
    
    # مدیریت ویرایش کاربر
    elif action == 'edit_user_search':
        username = text
        user = get_user_by_username(username)
        if not user:
            await update.message.reply_text("❌ کاربری با این نام پیدا نشد!")
            return
        
        await update.message.reply_text(
            f"✏️ **ویرایش کاربر: {username}**\n\n"
            f"👤 نام: {user['username']}\n"
            f"✅ وضعیت: {'فعال' if user['is_active'] else 'غیرفعال'}\n"
            f"📅 تاریخ: {user['created_at'][:10]}\n\n"
            f"چه چیزی را می‌خواهید ویرایش کنید؟\n"
            f"۱. نام کاربری\n"
            f"۲. وضعیت (فعال/غیرفعال)",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✏️ تغییر نام", callback_data=f"edit_name_{user['id']}")],
                [InlineKeyboardButton("🔄 تغییر وضعیت", callback_data=f"edit_status_{user['id']}")],
                [InlineKeyboardButton("🔙 بازگشت", callback_data="users_menu")]
            ])
        )
        context.user_data['action'] = None
    
    # مدیریت حذف کاربر
    elif action == 'delete_user_search':
        username = text
        user = get_user_by_username(username)
        if not user:
            await update.message.reply_text("❌ کاربری با این نام پیدا نشد!")
            return
        
        await update.message.reply_text(
            f"⚠️ **آیا از حذف کاربر {username} مطمئن هستید؟**\n\n"
            f"این عملیات غیرقابل بازگشت است!",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ بله، حذف کن", callback_data=f"confirm_delete_user_{user['id']}")],
                [InlineKeyboardButton("❌ انصراف", callback_data="users_menu")]
            ])
        )
        context.user_data['action'] = None
    
    # مدیریت جستجوی کاربر
    elif action == 'search_user_query':
        username = text
        user = get_user_by_username(username)
        if not user:
            await update.message.reply_text("❌ کاربری با این نام پیدا نشد!")
            return
        
        await update.message.reply_text(
            f"🔍 **نتیجه جستجو**\n\n"
            f"👤 نام: {user['username']}\n"
            f"🆔 آیدی: {user['id']}\n"
            f"✅ وضعیت: {'فعال' if user['is_active'] else 'غیرفعال'}\n"
            f"📅 تاریخ ثبت: {user['created_at'][:10]}\n\n"
            f"💳 موجودی: {user['balance']:,} تومان",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✏️ ویرایش", callback_data=f"edit_user_{user['id']}")],
                [InlineKeyboardButton("🔙 بازگشت", callback_data="users_menu")]
            ])
        )
        context.user_data['action'] = None
    
    # مدیریت تمدید اشتراک
    elif action == 'renew_sub_search':
        username = text
        user = get_user_by_username(username)
        if not user:
            await update.message.reply_text("❌ کاربری با این نام پیدا نشد!")
            return
        
        await update.message.reply_text(
            f"🔄 **تمدید اشتراک کاربر {username}**\n\n"
            f"لطفاً زمان جدید را وارد کنید:\n"
            f"مثال: ۲ روز، ۳ ساعت، ۱ ماه",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="subs_menu")]])
        )
        context.user_data['renew_username'] = username
        context.user_data['action'] = 'renew_sub_time'
    
    elif action == 'renew_sub_time':
        time_limit = text
        username = context.user_data.get('renew_username')
        user = get_user_by_username(username)
        
        if not user:
            await update.message.reply_text("❌ کاربر پیدا نشد!")
            return
        
        # پیدا کردن اشتراک کاربر
        conn = get_db()
        c = conn.cursor()
        c.execute('SELECT id FROM subscriptions WHERE user_id = ? ORDER BY created_at DESC LIMIT 1', (user['id'],))
        sub = c.fetchone()
        conn.close()
        
        if not sub:
            await update.message.reply_text("❌ این کاربر اشتراکی ندارد!")
            return
        
        update_subscription_time(sub['id'], time_limit)
        
        await update.message.reply_text(
            f"✅ **اشتراک کاربر {username} تمدید شد!**\n\n"
            f"⏰ زمان جدید: {time_limit}"
        )
        context.user_data['action'] = None
    
    # مدیریت حذف اشتراک
    elif action == 'delete_sub_search':
        username = text
        user = get_user_by_username(username)
        if not user:
            await update.message.reply_text("❌ کاربری با این نام پیدا نشد!")
            return
        
        # پیدا کردن اشتراک کاربر
        conn = get_db()
        c = conn.cursor()
        c.execute('SELECT id FROM subscriptions WHERE user_id = ? ORDER BY created_at DESC LIMIT 1', (user['id'],))
        sub = c.fetchone()
        conn.close()
        
        if not sub:
            await update.message.reply_text("❌ این کاربر اشتراکی ندارد!")
            return
        
        await update.message.reply_text(
            f"⚠️ **آیا از حذف اشتراک کاربر {username} مطمئن هستید؟**",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ بله، حذف کن", callback_data=f"confirm_delete_sub_{sub['id']}")],
                [InlineKeyboardButton("❌ انصراف", callback_data="subs_menu")]
            ])
        )
        context.user_data['action'] = None
    
    # مدیریت افزایش موجودی
    elif action == 'add_balance_input':
        parts = text.split()
        if len(parts) != 2:
            await update.message.reply_text("❌ فرمت صحیح: نام کاربری مبلغ\nمثال: alirez 50000")
            return
        
        username, amount_str = parts
        try:
            amount = int(amount_str)
        except:
            await update.message.reply_text("❌ مبلغ باید عدد باشد!")
            return
        
        user = get_user_by_username(username)
        if not user:
            await update.message.reply_text("❌ کاربری با این نام پیدا نشد!")
            return
        
        # افزایش موجودی
        conn = get_db()
        c = conn.cursor()
        c.execute('UPDATE users SET balance = balance + ? WHERE id = ?', (amount, user['id']))
        c.execute('INSERT INTO transactions (user_id, type, amount, description, created_at) VALUES (?, ?, ?, ?, ?)',
                  (user['id'], 'deposit', amount, f'شارژ کیف پول توسط ادمین', datetime.now().isoformat()))
        conn.commit()
        conn.close()
        
        await update.message.reply_text(
            f"✅ **موجودی کاربر {username} افزایش یافت!**\n\n"
            f"💰 مبلغ: {amount:,} تومان\n"
            f"💳 موجودی جدید: {user['balance'] + amount:,} تومان"
        )
        context.user_data['action'] = None

# ============================================================
# ===== اجرای ربات =====
# ============================================================

def main():
    print("=" * 50)
    print("🤖 ربات مدیریت پنل")
    print("=" * 50)
    print(f"👤 ادمین: {ADMIN_ID}")
    print("=" * 50)
    print("✅ ربات با موفقیت راه‌اندازی شد!")
    print("=" * 50)
    
    app = Application.builder().token(TOKEN).build()
    
    # دستور start
    app.add_handler(CommandHandler("start", start))
    
    # دکمه‌ها
    app.add_handler(CallbackQueryHandler(button_handler))
    
    # پیام‌های متنی
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    # فایل‌ها
    app.add_handler(MessageHandler(filters.Document.ALL, handle_bot_file))
    
    app.run_polling()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("🛑 ربات متوقف شد")
    except Exception as e:
        print(f"❌ خطا: {e}")
        import traceback
        traceback.print_exc()
