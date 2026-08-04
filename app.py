#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VROOM Panel v5.7 - پنل مدیریت ربات‌های تلگرام با سیستم اشتراک (ساب)
همه چیز در یک فایل - بدون نیاز به templates/
"""

import os
import sys
import time
import re
import json
import hashlib
import secrets
import sqlite3
import subprocess
import tempfile
import logging
import asyncio
import threading
import importlib
from datetime import datetime, timedelta
from urllib.parse import quote
from collections import deque, defaultdict

from flask import Flask, request, render_template_string, jsonify, redirect, url_for, make_response
import requests
import psutil

# ============================================================
# ===== تنظیمات اصلی =====
# ============================================================

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_urlsafe(32)

PORT = int(os.environ.get("PORT", 8080))
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")
MAX_CODE_SIZE = 500 * 1024

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("VROOM")

# ============================================================
# ===== دیتابیس =====
# ============================================================

DB_PATH = "vroom.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            password TEXT,
            link_token TEXT UNIQUE,
            storage_limit REAL DEFAULT 1,
            used_bytes INTEGER DEFAULT 0,
            max_connections INTEGER DEFAULT 0,
            expiry_days INTEGER DEFAULT 30,
            expires_at DATETIME,
            is_active BOOLEAN DEFAULT 1,
            created_at DATETIME,
            note TEXT
        )
    ''')
    
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
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')
    
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
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS bot_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_token TEXT,
            admin_id TEXT,
            is_active BOOLEAN DEFAULT 0,
            updated_at DATETIME
        )
    ''')
    
    conn.commit()
    conn.close()
    logger.info("✅ دیتابیس آماده شد")

init_db()

# ============================================================
# ===== توابع دیتابیس =====
# ============================================================

def db_execute(query, params=()):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(query, params)
    conn.commit()
    result = c.fetchall()
    conn.close()
    return result

def db_execute_one(query, params=()):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(query, params)
    conn.commit()
    result = c.fetchone()
    conn.close()
    return result

def hash_password(pw):
    return hashlib.sha256(f"{pw}{app.secret_key}".encode()).hexdigest()

def generate_link_token():
    return secrets.token_urlsafe(16)

def generate_access_code():
    import string
    alphabet = string.ascii_uppercase + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(8))

def create_user(username, password, storage_limit, expiry_days, max_connections=0):
    link_token = generate_link_token()
    hashed_pw = hash_password(password)
    expires_at = (datetime.now() + timedelta(days=expiry_days)).isoformat() if expiry_days > 0 else None
    
    db_execute('''
        INSERT INTO users (username, password, link_token, storage_limit, max_connections, expiry_days, expires_at, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (username, hashed_pw, link_token, storage_limit, max_connections, expiry_days, expires_at, datetime.now().isoformat()))
    
    return db_execute_one('SELECT id FROM users WHERE username = ?', (username,))

def get_user_by_username(username):
    return db_execute_one('SELECT * FROM users WHERE username = ?', (username,))

def get_user_by_token(token):
    return db_execute_one('SELECT * FROM users WHERE link_token = ?', (token,))

def get_user_by_id(user_id):
    return db_execute_one('SELECT * FROM users WHERE id = ?', (user_id,))

def get_all_users():
    return db_execute('SELECT * FROM users ORDER BY created_at DESC')

def delete_user(user_id):
    db_execute('DELETE FROM users WHERE id = ?', (user_id,))
    db_execute('DELETE FROM user_bots WHERE user_id = ?', (user_id,))

def is_user_expired(user):
    if not user or not user[8]:
        return False
    try:
        return datetime.now() >= datetime.fromisoformat(user[8])
    except:
        return False

def is_quota_exceeded(user):
    if not user:
        return False
    limit = user[4] * 1024 * 1024 * 1024
    return user[5] >= limit if limit > 0 else False

def get_user_status(user):
    if not user or not user[9]:
        return "غیرفعال"
    if is_user_expired(user):
        return "منقضی"
    if is_quota_exceeded(user):
        return "حجم تمام"
    return "فعال"

def get_domain():
    return (os.environ.get("RAILWAY_PUBLIC_DOMAIN") or "localhost").replace("https://", "").replace("http://", "").rstrip("/")

# ============================================================
# ===== توابع اجرای ربات کاربر =====
# ============================================================

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

def inject_token_to_code(code, token):
    code_with_token = re.sub(r'TOKEN\s*=\s*["\'][^"\']*["\']', f'TOKEN = "{token}"', code)
    if code_with_token == code:
        code_with_token = f'TOKEN = "{token}"\n\n' + code
    return code_with_token

def run_user_bot(code, token):
    try:
        temp_dir = tempfile.mkdtemp()
        temp_path = os.path.join(temp_dir, "user_bot.py")
        
        code_with_token = inject_token_to_code(code, token)
        
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

def save_user_bot(user_id, bot_token, bot_code, is_permanent):
    expires_at = datetime.now() + timedelta(days=1) if is_permanent else None
    db_execute('''
        INSERT INTO user_bots (user_id, bot_token, bot_code, status, is_permanent, created_at, expires_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (user_id, bot_token, bot_code, 'running', is_permanent, datetime.now().isoformat(),
          expires_at.isoformat() if expires_at else None))

# ============================================================
# ===== کد ربات تلگرام =====
# ============================================================

TELEGRAM_BOT_CODE = '''
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
import requests

TOKEN = "YOUR_BOT_TOKEN_HERE"
ADMIN_ID = 0

DB_PATH = "vroom.db"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def db_execute(query, params=()):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(query, params)
    conn.commit()
    result = c.fetchall()
    conn.close()
    return result

def db_execute_one(query, params=()):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(query, params)
    conn.commit()
    result = c.fetchone()
    conn.close()
    return result

def get_all_users():
    return db_execute('SELECT * FROM users ORDER BY created_at DESC')

def get_user_by_username(username):
    return db_execute_one('SELECT * FROM users WHERE username = ?', (username,))

def delete_user(user_id):
    db_execute('DELETE FROM users WHERE id = ?', (user_id,))

def generate_link_token():
    return secrets.token_urlsafe(16)

def generate_access_code():
    alphabet = string.ascii_uppercase + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(8))

def hash_password(pw):
    import hashlib
    return hashlib.sha256(f"{pw}VROOM_SECRET".encode()).hexdigest()

def create_user(username, password, storage_limit, expiry_days, max_connections=0):
    link_token = generate_link_token()
    hashed_pw = hash_password(password)
    expires_at = (datetime.now() + timedelta(days=expiry_days)).isoformat() if expiry_days > 0 else None
    db_execute('''
        INSERT INTO users (username, password, link_token, storage_limit, max_connections, expiry_days, expires_at, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (username, hashed_pw, link_token, storage_limit, max_connections, expiry_days, expires_at, datetime.now().isoformat()))
    return db_execute_one('SELECT id FROM users WHERE username = ?', (username,))

def ikb(rows):
    return {"inline_keyboard": [[{"text": t, "callback_data": c} for t, c in row] for row in rows]}

def main_menu():
    return ikb([
        [("➕ کاربر جدید", "add_user")],
        [("📋 لیست کاربران", "list_users")],
        [("📊 آمار", "stats")],
        [("ℹ️ راهنما", "help")],
    ])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if str(user.id) != str(ADMIN_ID):
        await update.message.reply_text("❌ شما دسترسی به این ربات ندارید!")
        return
    await update.message.reply_text(
        "🤖 **ربات مدیریت پنل**\\n\\n"
        "سلام ادمین عزیز!\\n"
        "لطفاً یکی از گزینه‌های زیر را انتخاب کنید:",
        reply_markup=main_menu()
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    if str(user.id) != str(ADMIN_ID):
        await query.edit_message_text("❌ شما دسترسی ندارید!")
        return
    data = query.data
    
    if data == "add_user":
        await query.edit_message_text(
            "➕ **افزودن کاربر جدید**\\n\\n"
            "لطفاً نام کاربری را وارد کنید:",
            reply_markup=ikb([[("❌ انصراف", "menu")]])
        )
        context.user_data['step'] = 'add_username'
    
    elif data == "list_users":
        users = get_all_users()
        if not users:
            await query.edit_message_text("📭 هیچ کاربری وجود ندارد.", reply_markup=main_menu())
            return
        text = "📋 **لیست کاربران:**\\n\\n"
        for i, u in enumerate(users[:10], 1):
            status = "✅ فعال" if u[9] else "❌ غیرفعال"
            text += f"{i}. {u[1]} - {u[4]}GB - {status}\\n"
        if len(users) > 10:
            text += f"\\n... و {len(users) - 10} کاربر دیگر"
        await query.edit_message_text(text, reply_markup=main_menu())
    
    elif data == "stats":
        users = get_all_users()
        total = len(users)
        active = sum(1 for u in users if u[9])
        text = f"📊 **آمار کلی:**\\n\\n"
        text += f"👤 کل کاربران: {total}\\n"
        text += f"✅ فعال: {active}\\n"
        text += f"❌ غیرفعال: {total - active}\\n"
        await query.edit_message_text(text, reply_markup=main_menu())
    
    elif data == "help":
        await query.edit_message_text(
            "ℹ️ **راهنما:**\\n\\n"
            "➕ کاربر جدید: ساخت کاربر با نام، رمز، حجم و زمان\\n"
            "📋 لیست کاربران: نمایش همه کاربران\\n"
            "📊 آمار: نمایش آمار کلی",
            reply_markup=main_menu()
        )
    
    elif data == "menu":
        await query.edit_message_text(
            "🤖 **ربات مدیریت پنل**\\n\\n"
            "لطفاً یکی از گزینه‌های زیر را انتخاب کنید:",
            reply_markup=main_menu()
        )
        context.user_data.clear()

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if str(user.id) != str(ADMIN_ID):
        return
    
    text = update.message.text.strip()
    step = context.user_data.get('step')
    
    if step == 'add_username':
        context.user_data['username'] = text
        context.user_data['step'] = 'add_password'
        await update.message.reply_text("🔑 **رمز عبور را وارد کنید:**")
    
    elif step == 'add_password':
        context.user_data['password'] = text
        context.user_data['step'] = 'add_storage'
        await update.message.reply_text("💾 **مقدار حافظه را به گیگابایت وارد کنید (مثلاً ۲):**")
    
    elif step == 'add_storage':
        try:
            storage = float(text)
            context.user_data['storage'] = storage
            context.user_data['step'] = 'add_days'
            await update.message.reply_text("📅 **مدت زمان را به روز وارد کنید (مثلاً ۳۰):**")
        except:
            await update.message.reply_text("❌ لطفاً یک عدد معتبر وارد کنید:")
    
    elif step == 'add_days':
        try:
            days = int(text)
            username = context.user_data.get('username')
            password = context.user_data.get('password')
            storage = context.user_data.get('storage')
            
            user = create_user(username, password, storage, days)
            link_token = generate_link_token()
            access_code = generate_access_code()
            
            db_execute('UPDATE users SET link_token = ?, password = ? WHERE username = ?', 
                      (link_token, hash_password(access_code), username))
            
            domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN") or "localhost"
            domain = domain.replace("https://", "").replace("http://", "").rstrip("/")
            
            await update.message.reply_text(
                f"✅ **کاربر با موفقیت ساخته شد!**\\n\\n"
                f"👤 نام کاربری: {username}\\n"
                f"🔑 رمز عبور: {access_code}\\n"
                f"💾 حافظه: {storage} GB\\n"
                f"📅 مدت: {days} روز\\n\\n"
                f"🔗 **لینک:**\\n"
                f"`https://{domain}/user/{link_token}`\\n\\n"
                f"💡 این اطلاعات را به کاربر ارسال کنید.",
                parse_mode='Markdown',
                reply_markup=main_menu()
            )
            context.user_data.clear()
        except Exception as e:
            await update.message.reply_text(f"❌ خطا: {e}")

def run_bot():
    try:
        app_bot = Application.builder().token(TOKEN).build()
        app_bot.add_handler(CommandHandler("start", start))
        app_bot.add_handler(CallbackQueryHandler(button_handler))
        app_bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        logger.info("🚀 ربات تلگرام شروع به کار کرد...")
        app_bot.run_polling()
    except Exception as e:
        logger.error(f"❌ خطا در ربات: {e}")
'''

# ============================================================
# ===== HTML صفحات (داخل خود app.py) =====
# ============================================================

INDEX_HTML = '''
<!DOCTYPE html>
<html dir="rtl" lang="fa">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🚀 راه‌انداز ربات تلگرام</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { background: linear-gradient(135deg, #0a0a1a, #1a1a3e); min-height: 100vh; color: #e2e8f0; font-family: "Segoe UI", Tahoma, Geneva, Verdana, sans-serif; }
        .container { max-width: 700px; margin: 0 auto; padding: 20px; }
        .card { background: rgba(255,255,255,0.05); backdrop-filter: blur(20px); border: 1px solid rgba(255,255,255,0.1); border-radius: 24px; padding: 30px; margin-bottom: 20px; }
        .card h1 { text-align: center; background: linear-gradient(135deg, #667eea, #764ba2); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
        .form-control { background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1); color: #e2e8f0; }
        .form-control:focus { background: rgba(255,255,255,0.1); border-color: #667eea; box-shadow: 0 0 0 3px rgba(102,126,234,0.15); color: #e2e8f0; }
        .btn-primary { background: linear-gradient(135deg, #667eea, #764ba2); border: none; padding: 12px 24px; font-weight: 600; }
        .btn-primary:hover { transform: translateY(-2px); box-shadow: 0 8px 30px rgba(102,126,234,0.4); }
        .toggle-container { display: flex; align-items: center; gap: 15px; background: rgba(255,255,255,0.03); border-radius: 12px; padding: 14px 18px; }
        .toggle-switch { position: relative; width: 50px; height: 28px; flex-shrink: 0; cursor: pointer; }
        .toggle-switch input { opacity: 0; width: 0; height: 0; }
        .toggle-slider { position: absolute; top: 0; left: 0; right: 0; bottom: 0; background: rgba(255,255,255,0.15); border-radius: 34px; transition: all 0.3s ease; }
        .toggle-slider::before { content: ""; position: absolute; height: 20px; width: 20px; left: 4px; bottom: 4px; background: #fff; border-radius: 50%; transition: all 0.3s ease; }
        .toggle-switch input:checked + .toggle-slider { background: linear-gradient(135deg, #667eea, #764ba2); }
        .toggle-switch input:checked + .toggle-slider::before { transform: translateX(22px); }
        .file-upload-wrapper { border: 2px dashed rgba(255,255,255,0.15); border-radius: 12px; padding: 30px 20px; text-align: center; cursor: pointer; transition: all 0.3s ease; }
        .file-upload-wrapper:hover { border-color: #667eea; background: rgba(102,126,234,0.05); }
        .file-upload-wrapper input[type="file"] { display: none; }
        .info-box { background: rgba(255,255,255,0.03); border-radius: 12px; padding: 16px 20px; border-right: 4px solid #667eea; }
    </style>
</head>
<body>
    <div class="container">
        <div class="card">
            <h1>🤖 راه‌انداز ربات تلگرام</h1>
            <p class="text-center text-secondary">کد ربات خود را آپلود کنید و اجرا کنید!</p>
            <form id="mainForm" method="POST" enctype="multipart/form-data" action="/api/user/run-bot">
                <input type="hidden" name="token" id="userToken" value="{{ token }}">
                <div class="mb-3">
                    <label class="form-label">📄 نوع کد:</label>
                    <select class="form-control" id="codeType" name="code_type">
                        <option value="file">📁 آپلود فایل (پایتون)</option>
                        <option value="text">✏️ چسباندن کد</option>
                    </select>
                </div>
                <div class="mb-3" id="fileInputGroup">
                    <div class="file-upload-wrapper" id="dropZone">
                        <input type="file" id="fileInput" name="code_file" accept=".py,.txt">
                        <span style="font-size:48px;">📤</span>
                        <div>کلیک کنید یا فایل را <span style="color:#667eea;">بکشید</span> اینجا</div>
                        <div class="text-secondary" style="font-size:12px;">فایل‌های .py و .txt (حداکثر ۵۰۰ کیلوبایت)</div>
                        <div id="fileNameDisplay" class="text-primary mt-2" style="display:none;">📎 <span id="fileName"></span></div>
                    </div>
                </div>
                <div class="mb-3" id="textInputGroup" style="display:none;">
                    <label class="form-label">✏️ کد ربات:</label>
                    <textarea class="form-control" id="codeText" name="code_text" rows="10" placeholder="# کد خود را اینجا بچسبانید..."></textarea>
                </div>
                <div class="mb-3">
                    <div class="toggle-container">
                        <div>
                            <div>🔄 حالت همیشه روشن</div>
                            <div style="font-size:12px;color:#718096;">اگر فعال باشد، ربات شما <span style="color:#68d391;">۲۴ ساعته</span> روشن می‌ماند</div>
                        </div>
                        <div class="toggle-switch">
                            <input type="checkbox" name="is_permanent" id="permanentToggle">
                            <span class="toggle-slider"></span>
                        </div>
                    </div>
                </div>
                <button type="submit" class="btn btn-primary w-100">🚀 بررسی و اجرا</button>
            </form>
        </div>
        <div id="resultContainer" class="card" style="display:none;">
            <div id="resultStatus" class="text-center"></div>
            <div class="mt-3">
                <label class="form-label">📋 لاگ‌های اجرا:</label>
                <pre id="resultLogs" style="background:rgba(0,0,0,0.5);padding:15px;border-radius:8px;max-height:300px;overflow-y:auto;color:#e2e8f0;font-size:13px;"></pre>
            </div>
        </div>
        <div class="info-box">
            <p>⚡ کد شما در محیط امن اجرا می‌شود.<br>🔒 لاگ‌ها و خطاها به شما نمایش داده می‌شوند.<br>⏱️ زمان اجرا نامحدود است.</p>
        </div>
    </div>
    <script>
        document.getElementById('codeType').addEventListener('change', function() {
            if (this.value === 'file') {
                document.getElementById('fileInputGroup').style.display = 'block';
                document.getElementById('textInputGroup').style.display = 'none';
            } else {
                document.getElementById('fileInputGroup').style.display = 'none';
                document.getElementById('textInputGroup').style.display = 'block';
            }
        });
        document.getElementById('dropZone').addEventListener('click', function() {
            document.getElementById('fileInput').click();
        });
        document.getElementById('fileInput').addEventListener('change', function() {
            if (this.files && this.files.length > 0) {
                document.getElementById('fileName').textContent = this.files[0].name;
                document.getElementById('fileNameDisplay').style.display = 'block';
            }
        });
        document.getElementById('permanentToggle').addEventListener('change', function() {
            document.querySelector('.toggle-container .toggle-slider').style.background = this.checked ? 'linear-gradient(135deg, #667eea, #764ba2)' : 'rgba(255,255,255,0.15)';
        });
        document.getElementById('mainForm').addEventListener('submit', async function(e) {
            e.preventDefault();
            const formData = new FormData(this);
            try {
                const response = await fetch('/api/user/run-bot', { method: 'POST', body: formData });
                const data = await response.json();
                document.getElementById('resultContainer').style.display = 'block';
                document.getElementById('resultStatus').innerHTML = data.success ? '<h4 class="text-success">✅ ' + data.message + '</h4>' : '<h4 class="text-danger">❌ ' + data.message + '</h4>';
                document.getElementById('resultLogs').textContent = data.logs || 'خروجی خاصی وجود ندارد.';
            } catch (error) {
                document.getElementById('resultContainer').style.display = 'block';
                document.getElementById('resultStatus').innerHTML = '<h4 class="text-danger">❌ خطا در ارتباط با سرور</h4>';
                document.getElementById('resultLogs').textContent = error.message;
            }
        });
    </script>
</body>
</html>
'''

USER_LOGIN_HTML = '''
<!DOCTYPE html>
<html dir="rtl" lang="fa">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🔐 ورود کاربر</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { background: linear-gradient(135deg, #0a0a1a, #1a1a3e); min-height: 100vh; display: flex; align-items: center; justify-content: center; color: #e2e8f0; font-family: "Segoe UI", Tahoma, Geneva, Verdana, sans-serif; }
        .card { background: rgba(255,255,255,0.05); backdrop-filter: blur(20px); border: 1px solid rgba(255,255,255,0.1); border-radius: 24px; padding: 40px; max-width: 420px; width: 100%; }
        .card h2 { text-align: center; background: linear-gradient(135deg, #667eea, #764ba2); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
        .form-control { background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1); color: #e2e8f0; }
        .form-control:focus { background: rgba(255,255,255,0.1); border-color: #667eea; box-shadow: 0 0 0 3px rgba(102,126,234,0.15); color: #e2e8f0; }
        .btn-primary { background: linear-gradient(135deg, #667eea, #764ba2); border: none; padding: 12px 24px; font-weight: 600; width: 100%; }
        .btn-primary:hover { transform: translateY(-2px); box-shadow: 0 8px 30px rgba(102,126,234,0.4); }
        .text-danger { color: #fc8181 !important; }
    </style>
</head>
<body>
    <div class="card">
        <h2>🔐 ورود به پنل کاربری</h2>
        {% if error %}
        <div class="alert alert-danger">{{ error }}</div>
        {% endif %}
        <form method="POST">
            <div class="mb-3">
                <label class="form-label">🔑 رمز عبور</label>
                <input type="password" name="password" class="form-control" placeholder="رمز عبور خود را وارد کنید" required>
            </div>
            <button type="submit" class="btn btn-primary">🔓 ورود</button>
        </form>
        <div class="mt-3 text-center text-secondary" style="font-size:12px;">
            💡 این لینک مختص شماست. رمز عبور را از ادمین دریافت کنید.
        </div>
    </div>
</body>
</html>
'''

USER_DASHBOARD_HTML = '''
<!DOCTYPE html>
<html dir="rtl" lang="fa">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>📊 پنل کاربری</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { background: linear-gradient(135deg, #0a0a1a, #1a1a3e); min-height: 100vh; color: #e2e8f0; font-family: "Segoe UI", Tahoma, Geneva, Verdana, sans-serif; }
        .container { max-width: 700px; margin: 0 auto; padding: 20px; }
        .card { background: rgba(255,255,255,0.05); backdrop-filter: blur(20px); border: 1px solid rgba(255,255,255,0.1); border-radius: 24px; padding: 30px; margin-bottom: 20px; }
        .card h2 { text-align: center; background: linear-gradient(135deg, #667eea, #764ba2); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
        .stat { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; text-align: center; }
        .stat-item { background: rgba(255,255,255,0.03); border-radius: 12px; padding: 12px; }
        .stat-item .number { font-size: 24px; font-weight: 700; }
        .stat-item .label { font-size: 12px; color: #718096; }
        .progress-bar-custom { height: 8px; background: rgba(255,255,255,0.05); border-radius: 99px; overflow: hidden; margin-top: 10px; }
        .progress-bar-custom .fill { height: 100%; border-radius: 99px; background: linear-gradient(90deg, #667eea, #764ba2); transition: width 0.5s ease; }
        .btn-primary { background: linear-gradient(135deg, #667eea, #764ba2); border: none; padding: 12px 24px; font-weight: 600; }
        .btn-primary:hover { transform: translateY(-2px); box-shadow: 0 8px 30px rgba(102,126,234,0.4); }
    </style>
</head>
<body>
    <div class="container">
        <div class="card">
            <h2>📊 پنل کاربری</h2>
            <p class="text-center text-secondary">سلام {{ user[1] }} عزیز!</p>
            <div class="stat">
                <div class="stat-item">
                    <div class="number" style="color:#68d391;">{{ used_gb }}</div>
                    <div class="label">مصرف (GB)</div>
                </div>
                <div class="stat-item">
                    <div class="number" style="color:#667eea;">{{ limit_gb }}</div>
                    <div class="label">حافظه (GB)</div>
                </div>
                <div class="stat-item">
                    <div class="number" style="color:#fcd34d;">{{ percent }}%</div>
                    <div class="label">درصد مصرف</div>
                </div>
                <div class="stat-item">
                    <div class="number" style="color:{% if status == 'فعال' %}#68d391{% elif status == 'منقضی' %}#fc8181{% elif status == 'حجم تمام' %}#f59e0b{% else %}#718096{% endif %};">{{ status }}</div>
                    <div class="label">وضعیت</div>
                </div>
            </div>
            <div class="progress-bar-custom"><div class="fill" style="width:{{ percent }}%;"></div></div>
        </div>
        <div class="card">
            <h3 style="text-align:center;color:#667eea;">🚀 اجرای ربات</h3>
            <p class="text-center text-secondary">کد ربات خود را آپلود کنید و اجرا کنید!</p>
            <form id="dashboardForm" method="POST" enctype="multipart/form-data" action="/api/user/run-bot">
                <input type="hidden" name="token" value="{{ user[3] }}">
                <div class="mb-3">
                    <label class="form-label">📄 نوع کد:</label>
                    <select class="form-control" id="dashCodeType" name="code_type">
                        <option value="file">📁 آپلود فایل (پایتون)</option>
                        <option value="text">✏️ چسباندن کد</option>
                    </select>
                </div>
                <div class="mb-3" id="dashFileGroup">
                    <div class="file-upload-wrapper" style="border:2px dashed rgba(255,255,255,0.15);border-radius:12px;padding:30px 20px;text-align:center;cursor:pointer;">
                        <input type="file" id="dashFileInput" name="code_file" accept=".py,.txt" style="display:none;">
                        <span style="font-size:48px;">📤</span>
                        <div>کلیک کنید یا فایل را بکشید</div>
                    </div>
                </div>
                <div class="mb-3" id="dashTextGroup" style="display:none;">
                    <textarea class="form-control" id="dashCodeText" name="code_text" rows="8" placeholder="# کد خود را اینجا بچسبانید..."></textarea>
                </div>
                <div class="mb-3">
                    <div class="toggle-container" style="display:flex;align-items:center;gap:15px;background:rgba(255,255,255,0.03);border-radius:12px;padding:14px 18px;">
                        <div>
                            <div>🔄 همیشه روشن</div>
                            <div style="font-size:12px;color:#718096;">۲۴ ساعته روشن می‌ماند</div>
                        </div>
                        <div class="toggle-switch" style="position:relative;width:50px;height:28px;flex-shrink:0;cursor:pointer;">
                            <input type="checkbox" name="is_permanent" id="dashPermanent">
                            <span class="toggle-slider" style="position:absolute;top:0;left:0;right:0;bottom:0;background:rgba(255,255,255,0.15);border-radius:34px;transition:all 0.3s ease;"></span>
                        </div>
                    </div>
                </div>
                <button type="submit" class="btn btn-primary w-100">🚀 اجرا</button>
            </form>
            <div id="dashResult" class="mt-3" style="display:none;">
                <div id="dashResultStatus" class="text-center"></div>
                <pre id="dashResultLogs" style="background:rgba(0,0,0,0.5);padding:15px;border-radius:8px;max-height:200px;overflow-y:auto;color:#e2e8f0;font-size:13px;margin-top:10px;"></pre>
            </div>
        </div>
    </div>
    <script>
        document.getElementById('dashCodeType').addEventListener('change', function() {
            if (this.value === 'file') {
                document.getElementById('dashFileGroup').style.display = 'block';
                document.getElementById('dashTextGroup').style.display = 'none';
            } else {
                document.getElementById('dashFileGroup').style.display = 'none';
                document.getElementById('dashTextGroup').style.display = 'block';
            }
        });
        document.querySelector('.file-upload-wrapper').addEventListener('click', function() {
            document.getElementById('dashFileInput').click();
        });
        document.getElementById('dashPermanent').addEventListener('change', function() {
            this.parentElement.querySelector('.toggle-slider').style.background = this.checked ? 'linear-gradient(135deg, #667eea, #764ba2)' : 'rgba(255,255,255,0.15)';
        });
        document.getElementById('dashboardForm').addEventListener('submit', async function(e) {
            e.preventDefault();
            const formData = new FormData(this);
            try {
                const response = await fetch('/api/user/run-bot', { method: 'POST', body: formData });
                const data = await response.json();
                document.getElementById('dashResult').style.display = 'block';
                document.getElementById('dashResultStatus').innerHTML = data.success ? '<h4 class="text-success">✅ ' + data.message + '</h4>' : '<h4 class="text-danger">❌ ' + data.message + '</h4>';
                document.getElementById('dashResultLogs').textContent = data.logs || 'خروجی خاصی وجود ندارد.';
            } catch (error) {
                document.getElementById('dashResult').style.display = 'block';
                document.getElementById('dashResultStatus').innerHTML = '<h4 class="text-danger">❌ خطا در ارتباط با سرور</h4>';
                document.getElementById('dashResultLogs').textContent = error.message;
            }
        });
    </script>
</body>
</html>
'''

ADMIN_LOGIN_HTML = '''
<!DOCTYPE html>
<html dir="rtl" lang="fa">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🔐 ورود ادمین</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { background: linear-gradient(135deg, #0a0a1a, #1a1a3e); min-height: 100vh; display: flex; align-items: center; justify-content: center; color: #e2e8f0; font-family: "Segoe UI", Tahoma, Geneva, Verdana, sans-serif; }
        .card { background: rgba(255,255,255,0.05); backdrop-filter: blur(20px); border: 1px solid rgba(255,255,255,0.1); border-radius: 24px; padding: 40px; max-width: 420px; width: 100%; }
        .card h2 { text-align: center; background: linear-gradient(135deg, #667eea, #764ba2); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
        .form-control { background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1); color: #e2e8f0; }
        .form-control:focus { background: rgba(255,255,255,0.1); border-color: #667eea; box-shadow: 0 0 0 3px rgba(102,126,234,0.15); color: #e2e8f0; }
        .btn-primary { background: linear-gradient(135deg, #667eea, #764ba2); border: none; padding: 12px 24px; font-weight: 600; width: 100%; }
        .btn-primary:hover { transform: translateY(-2px); box-shadow: 0 8px 30px rgba(102,126,234,0.4); }
    </style>
</head>
<body>
    <div class="card">
        <h2>🔐 ورود ادمین</h2>
        {% if error %}
        <div class="alert alert-danger">{{ error }}</div>
        {% endif %}
        <form method="POST">
            <div class="mb-3">
                <label class="form-label">🔑 رمز عبور</label>
                <input type="password" name="password" class="form-control" placeholder="رمز عبور ادمین را وارد کنید" required>
            </div>
            <button type="submit" class="btn btn-primary">🔓 ورود</button>
        </form>
    </div>
</body>
</html>
'''

ADMIN_DASHBOARD_HTML = '''
<!DOCTYPE html>
<html dir="rtl" lang="fa">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>📊 پنل ادمین</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { background: linear-gradient(135deg, #0a0a1a, #1a1a3e); min-height: 100vh; color: #e2e8f0; font-family: "Segoe UI", Tahoma, Geneva, Verdana, sans-serif; }
        .container { max-width: 900px; margin: 0 auto; padding: 20px; }
        .card { background: rgba(255,255,255,0.05); backdrop-filter: blur(20px); border: 1px solid rgba(255,255,255,0.1); border-radius: 24px; padding: 30px; margin-bottom: 20px; }
        .card h2 { text-align: center; background: linear-gradient(135deg, #667eea, #764ba2); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
        .stat { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; text-align: center; }
        .stat-item { background: rgba(255,255,255,0.03); border-radius: 12px; padding: 12px; }
        .stat-item .number { font-size: 24px; font-weight: 700; }
        .stat-item .label { font-size: 12px; color: #718096; }
        table { width: 100%; border-collapse: collapse; margin-top: 15px; }
        th { text-align: right; padding: 8px; color: #718096; border-bottom: 1px solid rgba(255,255,255,0.05); }
        td { padding: 8px; border-bottom: 1px solid rgba(255,255,255,0.03); }
        .btn-primary { background: linear-gradient(135deg, #667eea, #764ba2); border: none; padding: 10px 20px; font-weight: 600; }
        .btn-danger { background: linear-gradient(135deg, #fc8181, #e53e3e); border: none; padding: 10px 20px; font-weight: 600; }
        .btn-success { background: linear-gradient(135deg, #48bb78, #38a169); border: none; padding: 10px 20px; font-weight: 600; }
        .btn-sm { padding: 4px 10px; font-size: 12px; }
    </style>
</head>
<body>
    <div class="container">
        <div class="card">
            <h2>📊 پنل ادمین</h2>
            <p class="text-center text-secondary">مدیریت کاربران و سیستم</p>
            <div class="stat">
                <div class="stat-item">
                    <div class="number" style="color:#667eea;">{{ total }}</div>
                    <div class="label">کل کاربران</div>
                </div>
                <div class="stat-item">
                    <div class="number" style="color:#68d391;">{{ active }}</div>
                    <div class="label">فعال</div>
                </div>
                <div class="stat-item">
                    <div class="number" style="color:#fc8181;">{{ total - active }}</div>
                    <div class="label">غیرفعال</div>
                </div>
                <div class="stat-item">
                    <div class="number" style="color:#fcd34d;">{{ domain }}</div>
                    <div class="label">دامنه</div>
                </div>
            </div>
        </div>
        <div class="card">
            <div class="d-flex justify-content-between align-items-center mb-3">
                <h3 style="color:#667eea;margin:0;">👤 مدیریت کاربران</h3>
                <button class="btn btn-success" onclick="showAddUser()">➕ کاربر جدید</button>
            </div>
            <div id="addUserForm" style="display:none;background:rgba(255,255,255,0.03);border-radius:12px;padding:15px;margin-bottom:15px;">
                <div class="row g-2">
                    <div class="col-md-3"><input type="text" id="newUsername" class="form-control" placeholder="نام کاربری"></div>
                    <div class="col-md-3"><input type="text" id="newPassword" class="form-control" placeholder="رمز عبور"></div>
                    <div class="col-md-2"><input type="number" id="newStorage" class="form-control" placeholder="حجم (GB)" value="1"></div>
                    <div class="col-md-2"><input type="number" id="newDays" class="form-control" placeholder="روز" value="30"></div>
                    <div class="col-md-2"><button class="btn btn-primary w-100" onclick="createUser()">ساخت</button></div>
                </div>
            </div>
            <div style="overflow-x:auto;">
                <table>
                    <thead>
                        <tr><th>#</th><th>نام کاربری</th><th>حجم (GB)</th><th>مصرف</th><th>وضعیت</th><th>عملیات</th></tr>
                    </thead>
                    <tbody id="usersTableBody">
                        {% for user in users %}
                        <tr>
                            <td>{{ loop.index }}</td>
                            <td>{{ user[1] }}</td>
                            <td>{{ user[4] }}</td>
                            <td>{{ (user[5] / 1024**3)|round(2) }}</td>
                            <td><span style="color:{% if user[9] %}#68d391{% else %}#fc8181{% endif %};">{% if user[9] %}فعال{% else %}غیرفعال{% endif %}</span></td>
                            <td>
                                <button class="btn btn-danger btn-sm" onclick="deleteUser({{ user[0] }})">🗑️</button>
                            </td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        </div>
        <div class="text-center text-secondary" style="font-size:12px;">
            <a href="/admin/bot-settings" style="color:#667eea;">⚙️ تنظیمات ربات</a> | 
            <a href="/" style="color:#667eea;">🏠 صفحه اصلی</a>
        </div>
    </div>
    <script>
        function showAddUser() {
            const form = document.getElementById('addUserForm');
            form.style.display = form.style.display === 'none' ? 'block' : 'none';
        }
        async function createUser() {
            const username = document.getElementById('newUsername').value.trim();
            const password = document.getElementById('newPassword').value.trim();
            const storage = document.getElementById('newStorage').value;
            const days = document.getElementById('newDays').value;
            if (!username || !password) { alert('نام کاربری و رمز عبور الزامی است!'); return; }
            const formData = new FormData();
            formData.append('username', username);
            formData.append('password', password);
            formData.append('storage', storage);
            formData.append('days', days);
            try {
                const response = await fetch('/admin/create-user', { method: 'POST', body: formData });
                const data = await response.json();
                alert(data.message);
                if (data.success) location.reload();
            } catch (error) { alert('خطا: ' + error.message); }
        }
        async function deleteUser(userId) {
            if (!confirm('آیا از حذف این کاربر مطمئن هستید؟')) return;
            try {
                const response = await fetch('/admin/delete-user/' + userId, { method: 'POST' });
                const data = await response.json();
                alert(data.message);
                if (data.success) location.reload();
            } catch (error) { alert('خطا: ' + error.message); }
        }
    </script>
</body>
</html>
'''

ADMIN_BOT_SETTINGS_HTML = '''
<!DOCTYPE html>
<html dir="rtl" lang="fa">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>⚙️ تنظیمات ربات</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { background: linear-gradient(135deg, #0a0a1a, #1a1a3e); min-height: 100vh; color: #e2e8f0; font-family: "Segoe UI", Tahoma, Geneva, Verdana, sans-serif; }
        .container { max-width: 600px; margin: 0 auto; padding: 20px; }
        .card { background: rgba(255,255,255,0.05); backdrop-filter: blur(20px); border: 1px solid rgba(255,255,255,0.1); border-radius: 24px; padding: 30px; margin-bottom: 20px; }
        .card h2 { text-align: center; background: linear-gradient(135deg, #667eea, #764ba2); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
        .form-control { background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1); color: #e2e8f0; }
        .form-control:focus { background: rgba(255,255,255,0.1); border-color: #667eea; box-shadow: 0 0 0 3px rgba(102,126,234,0.15); color: #e2e8f0; }
        .btn-primary { background: linear-gradient(135deg, #667eea, #764ba2); border: none; padding: 12px 24px; font-weight: 600; width: 100%; }
        .btn-primary:hover { transform: translateY(-2px); box-shadow: 0 8px 30px rgba(102,126,234,0.4); }
        .form-switch .form-check-input { background-color: rgba(255,255,255,0.1); border: none; width: 44px; height: 24px; cursor: pointer; }
        .form-switch .form-check-input:checked { background-color: #667eea; }
    </style>
</head>
<body>
    <div class="container">
        <div class="card">
            <h2>⚙️ تنظیمات ربات تلگرام</h2>
            {% if success %}
            <div class="alert alert-success">{{ success }}</div>
            {% endif %}
            <form method="POST">
                <div class="mb-3">
                    <label class="form-label">🤖 توکن ربات</label>
                    <input type="text" name="bot_token" class="form-control" value="{{ bot_token }}" placeholder="توکن ربات را وارد کنید">
                </div>
                <div class="mb-3">
                    <label class="form-label">👤 آیدی عددی ادمین</label>
                    <input type="text" name="admin_id" class="form-control" value="{{ admin_id }}" placeholder="آیدی ادمین را وارد کنید">
                </div>
                <div class="mb-3">
                    <div class="form-check form-switch">
                        <input class="form-check-input" type="checkbox" name="is_active" {% if is_active %}checked{% endif %}>
                        <label class="form-check-label" style="color:#a0aec0;">✅ فعال بودن ربات</label>
                    </div>
                </div>
                <button type="submit" class="btn btn-primary">💾 ذخیره تنظیمات</button>
            </form>
            <div class="mt-3 text-center">
                <a href="/admin/dashboard" style="color:#667eea;">🔙 بازگشت به پنل ادمین</a>
            </div>
        </div>
    </div>
</body>
</html>
'''

ERROR_HTML = '''
<!DOCTYPE html>
<html dir="rtl" lang="fa">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>❌ خطا</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { background: linear-gradient(135deg, #0a0a1a, #1a1a3e); min-height: 100vh; display: flex; align-items: center; justify-content: center; color: #e2e8f0; font-family: "Segoe UI", Tahoma, Geneva, Verdana, sans-serif; }
        .card { background: rgba(255,255,255,0.05); backdrop-filter: blur(20px); border: 1px solid rgba(255,255,255,0.1); border-radius: 24px; padding: 40px; max-width: 500px; width: 100%; text-align: center; }
        .card h1 { font-size: 48px; }
        .btn-primary { background: linear-gradient(135deg, #667eea, #764ba2); border: none; padding: 12px 24px; font-weight: 600; }
        .btn-primary:hover { transform: translateY(-2px); box-shadow: 0 8px 30px rgba(102,126,234,0.4); }
    </style>
</head>
<body>
    <div class="card">
        <h1>❌</h1>
        <h2 style="color:#fc8181;">{{ message }}</h2>
        <p class="text-secondary">لطفاً دوباره تلاش کنید.</p>
        <a href="/" class="btn btn-primary">🏠 بازگشت به صفحه اصلی</a>
    </div>
</body>
</html>
'''

# ============================================================
# ===== مسیرهای سایت =====
# ============================================================

@app.route('/')
def home():
    return render_template_string(INDEX_HTML)

@app.route('/user/<token>', methods=['GET', 'POST'])
def user_panel(token):
    user = get_user_by_token(token)
    if not user:
        return render_template_string(ERROR_HTML, message="❌ لینک نامعتبر است!")
    
    if request.method == 'POST':
        password = request.form.get('password')
        if hash_password(password) != user[2]:
            return render_template_string(USER_LOGIN_HTML, token=token, error="❌ رمز عبور اشتباه است!")
        
        resp = make_response(redirect(url_for('user_dashboard', token=token)))
        resp.set_cookie('user_auth', token, max_age=3600*24*7)
        return resp
    
    return render_template_string(USER_LOGIN_HTML, token=token, user=user)

@app.route('/dashboard/<token>')
def user_dashboard(token):
    auth_token = request.cookies.get('user_auth')
    if auth_token != token:
        return redirect(url_for('user_panel', token=token))
    
    user = get_user_by_token(token)
    if not user:
        return render_template_string(ERROR_HTML, message="❌ کاربر یافت نشد!")
    
    status = get_user_status(user)
    used_gb = round(user[5] / (1024**3), 2)
    limit_gb = user[4]
    percent = min(100, round((user[5] / (limit_gb * 1024**3)) * 100, 2)) if limit_gb > 0 else 0
    
    return render_template_string(USER_DASHBOARD_HTML, 
                                  user=user, 
                                  status=status,
                                  used_gb=used_gb,
                                  limit_gb=limit_gb,
                                  percent=percent,
                                  domain=get_domain())

@app.route('/api/user/run-bot', methods=['POST'])
def run_user_bot_api():
    token = request.form.get('token')
    code_type = request.form.get('code_type', 'file')
    code_content = request.form.get('code_text', '')
    is_permanent = request.form.get('is_permanent') == 'on'
    
    user = get_user_by_token(token)
    if not user:
        return jsonify({'success': False, 'message': '❌ کاربر یافت نشد!'})
    
    if is_user_expired(user):
        return jsonify({'success': False, 'message': '❌ اشتراک شما منقضی شده است!'})
    
    if is_quota_exceeded(user):
        return jsonify({'success': False, 'message': '❌ حجم مصرفی شما تمام شده است!'})
    
    if code_type == 'file':
        if 'code_file' not in request.files:
            return jsonify({'success': False, 'message': '❌ لطفاً یک فایل انتخاب کنید!'})
        file = request.files['code_file']
        if file.filename == '':
            return jsonify({'success': False, 'message': '❌ لطفاً یک فایل انتخاب کنید!'})
        code_content = file.read().decode('utf-8', errors='ignore')
    
    bot_token = extract_token_from_code(code_content)
    if not bot_token:
        return jsonify({'success': False, 'message': '❌ توکن در کد پیدا نشد!', 'need_token': True})
    
    if not check_token_valid(bot_token):
        return jsonify({'success': False, 'message': '❌ توکن نامعتبر است!'})
    
    save_user_bot(user[0], bot_token, code_content, is_permanent)
    success, msg = run_user_bot(code_content, bot_token)
    
    if success:
        return jsonify({'success': True, 'message': '✅ ربات با موفقیت اجرا شد!', 'bot_token': bot_token})
    else:
        return jsonify({'success': False, 'message': msg})

@app.route('/admin', methods=['GET', 'POST'])
def admin_panel():
    if request.method == 'POST':
        password = request.form.get('password')
        if hash_password(password) == hash_password(ADMIN_PASSWORD):
            resp = make_response(redirect(url_for('admin_dashboard')))
            resp.set_cookie('admin_auth', 'true', max_age=3600*24*7)
            return resp
        return render_template_string(ADMIN_LOGIN_HTML, error="❌ رمز عبور اشتباه است!")
    
    return render_template_string(ADMIN_LOGIN_HTML)

@app.route('/admin/dashboard')
def admin_dashboard():
    if request.cookies.get('admin_auth') != 'true':
        return redirect(url_for('admin_panel'))
    
    users = get_all_users()
    total = len(users)
    active = sum(1 for u in users if u[9])
    
    return render_template_string(ADMIN_DASHBOARD_HTML, 
                                  users=users,
                                  total=total,
                                  active=active,
                                  domain=get_domain())

@app.route('/admin/create-user', methods=['POST'])
def admin_create_user():
    if request.cookies.get('admin_auth') != 'true':
        return jsonify({'success': False, 'message': '❌ دسترسی غیرمجاز!'})
    
    username = request.form.get('username')
    password = request.form.get('password')
    storage = float(request.form.get('storage', 1))
    days = int(request.form.get('days', 30))
    
    if get_user_by_username(username):
        return jsonify({'success': False, 'message': '❌ این نام کاربری قبلاً ثبت شده است!'})
    
    user = create_user(username, password, storage, days)
    if user:
        return jsonify({'success': True, 'message': '✅ کاربر با موفقیت ساخته شد!'})
    return jsonify({'success': False, 'message': '❌ خطا در ساخت کاربر!'})

@app.route('/admin/delete-user/<int:user_id>', methods=['POST'])
def admin_delete_user(user_id):
    if request.cookies.get('admin_auth') != 'true':
        return jsonify({'success': False, 'message': '❌ دسترسی غیرمجاز!'})
    
    delete_user(user_id)
    return jsonify({'success': True, 'message': '✅ کاربر حذف شد!'})

@app.route('/admin/bot-settings', methods=['GET', 'POST'])
def admin_bot_settings():
    if request.cookies.get('admin_auth') != 'true':
        return redirect(url_for('admin_panel'))
    
    if request.method == 'POST':
        bot_token = request.form.get('bot_token')
        admin_id = request.form.get('admin_id')
        is_active = request.form.get('is_active') == 'on'
        
        db_execute('''
            INSERT OR REPLACE INTO bot_settings (id, bot_token, admin_id, is_active, updated_at)
            VALUES (1, ?, ?, ?, ?)
        ''', (bot_token, admin_id, is_active, datetime.now().isoformat()))
        
        update_bot_code(bot_token, admin_id)
        
        return render_template_string(ADMIN_BOT_SETTINGS_HTML, 
                                      success="✅ تنظیمات ذخیره شد!",
                                      bot_token=bot_token,
                                      admin_id=admin_id,
                                      is_active=is_active)
    
    settings = db_execute_one('SELECT * FROM bot_settings WHERE id = 1')
    return render_template_string(ADMIN_BOT_SETTINGS_HTML,
                                  bot_token=settings[1] if settings else '',
                                  admin_id=settings[2] if settings else '',
                                  is_active=settings[3] if settings else False)

def update_bot_code(bot_token, admin_id):
    global TELEGRAM_BOT_CODE
    TELEGRAM_BOT_CODE = TELEGRAM_BOT_CODE.replace('YOUR_BOT_TOKEN_HERE', bot_token)
    TELEGRAM_BOT_CODE = TELEGRAM_BOT_CODE.replace('ADMIN_ID = 0', f'ADMIN_ID = {admin_id}')
    
    with open('bot_runner.py', 'w', encoding='utf-8') as f:
        f.write(TELEGRAM_BOT_CODE)

# ============================================================
# ===== اجرای ربات در پس‌زمینه =====
# ============================================================

def run_telegram_bot():
    try:
        settings = db_execute_one('SELECT bot_token, is_active FROM bot_settings WHERE id = 1')
        if settings and settings[1]:
            with open('bot_runner.py', 'w', encoding='utf-8') as f:
                f.write(TELEGRAM_BOT_CODE)
            subprocess.Popen([sys.executable, 'bot_runner.py'])
            logger.info("🚀 ربات تلگرام در پس‌زمینه اجرا شد")
    except Exception as e:
        logger.error(f"❌ خطا در اجرای ربات: {e}")

# ============================================================
# ===== راه‌اندازی =====
# ============================================================

if __name__ == '__main__':
    print("\n" + "="*60)
    print("🚀 VROOM پنل مدیریت ربات‌های تلگرام")
    print("="*60)
    print(f"📡 پورت: {PORT}")
    print(f"🌐 آدرس: http://localhost:{PORT}")
    print("👤 ادمین: admin")
    print(f"🔑 رمز پیش‌فرض: {ADMIN_PASSWORD}")
    print("="*60 + "\n")
    
    threading.Thread(target=run_telegram_bot, daemon=True).start()
    
    app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True)
