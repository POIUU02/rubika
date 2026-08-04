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
app.secret_key = 'your-secret-key-here'

# ==================== تنظیمات ====================
PORT = int(os.environ.get('PORT', 8080))
MAX_CODE_SIZE = 500 * 1024
ADMIN_ID = 6443963679
ADMIN_BOT_TOKEN = "8262116870:AAGhf7siH7qpVm4nPAM8kGJcPwgyu0PZZFo"

logging.basicConfig(level=logging.INFO)
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
    
    # جدول کاربران
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        telegram_id TEXT,
        password TEXT,
        balance INTEGER DEFAULT 0,
        is_active BOOLEAN DEFAULT 1,
        created_at DATETIME
    )''')
    
    # جدول اشتراک‌ها
    c.execute('''CREATE TABLE IF NOT EXISTS subscriptions (
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
        created_at DATETIME
    )''')
    
    # جدول ربات‌ها
    c.execute('''CREATE TABLE IF NOT EXISTS user_bots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        bot_token TEXT,
        bot_code TEXT,
        status TEXT DEFAULT 'stopped',
        is_permanent BOOLEAN DEFAULT 0,
        created_at DATETIME,
        expires_at DATETIME,
        last_run DATETIME
    )''')
    
    conn.commit()
    conn.close()
    logger.info("✅ دیتابیس آماده است")

init_db()

# ==================== توابع کمکی ====================

def generate_link_token():
    return secrets.token_urlsafe(16)

def generate_access_code():
    alphabet = string.ascii_uppercase + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(8))

def parse_time_limit(time_str):
    units = {'ثانیه': 1, 'دقیقه': 60, 'ساعت': 3600, 'روز': 86400, 'هفته': 604800, 'ماه': 2592000, 'سال': 31536000}
    match = re.match(r'(\d+\.?\d*)\s*([^\d]+)', time_str.strip().lower())
    if not match:
        return None
    number = float(match.group(1))
    unit = match.group(2).strip()
    for key, value in units.items():
        if key in unit:
            return int(number * value)
    return None

def create_subscription(user_id, username, storage_limit, time_limit_str):
    """ایجاد اشتراک جدید با تورفتگی صحیح"""
    link_token = generate_link_token()
    access_code = generate_access_code()
    time_seconds = parse_time_limit(time_limit_str)
    expires_at = datetime.now() + timedelta(seconds=time_seconds) if time_seconds else None
    
    conn = get_db()
    c = conn.cursor()
    c.execute('''
        INSERT INTO subscriptions (
            user_id, username, link_token, access_code, storage_limit,
            time_limit, time_limit_seconds, expires_at, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (user_id, username, link_token, access_code, storage_limit,
          time_limit_str, time_seconds,
          expires_at.isoformat() if expires_at else None,
          datetime.now().isoformat()))
    conn.commit()
    conn.close()
    
    return link_token, access_code

def check_subscription_valid(user_id):
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

def inject_token_to_code(code, token):
    """تزریق توکن به کد کاربر"""
    modified_code = re.sub(r'TOKEN\s*=\s*["\'][^"\']*["\']', f'TOKEN = "{token}"', code)
    modified_code = re.sub(r'token\s*=\s*["\'][^"\']*["\']', f'token = "{token}"', modified_code)
    if 'TOKEN' not in modified_code and 'token' not in modified_code:
        modified_code = f'TOKEN = "{token}"\n\n' + code
    return modified_code

def execute_user_bot(code, token, user_id, is_permanent=False):
    """اجرای ربات کاربر"""
    subscription = check_subscription_valid(user_id)
    if not subscription:
        return {'success': False, 'message': '❌ اشتراک شما منقضی شده است!', 'logs': ''}
    
    code_with_token = inject_token_to_code(code, token)
    
    temp_dir = tempfile.mkdtemp()
    temp_path = os.path.join(temp_dir, "user_bot.py")
    
    try:
        with open(temp_path, 'w', encoding='utf-8') as f:
            f.write(code_with_token)
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return {'success': False, 'message': f'❌ خطا: {str(e)}', 'logs': str(e)}
    
    install_logs = ""
    try:
        subprocess.run([sys.executable, "-m", "pip", "install", "python-telegram-bot", "--quiet"],
                      capture_output=True, timeout=60)
        install_logs = "✅ python-telegram-bot نصب شد.\n"
    except:
        install_logs = "⚠️ خطا در نصب python-telegram-bot\n"
    
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
            shutil.rmtree(temp_dir, ignore_errors=True)
        except:
            pass
    
    full_output = install_logs + output if install_logs else output
    
    return {
        'success': success,
        'message': '✅ ربات با موفقیت اجرا شد!' if success else f'❌ خطا: {error_msg}',
        'logs': full_output,
        'error': error_msg if not success else ''
    }

# ==================== مسیرهای سایت ====================

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/', methods=['POST'])
def index_post():
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
        else:
            code_content = request.form.get('code_text', '').strip()
        
        if not code_content:
            return render_template('result.html',
                result={'success': False, 'message': '❌ لطفاً کد را وارد کنید', 'logs': ''})
        
        # بررسی توکن
        try:
            url = f"https://api.telegram.org/bot{token}/getMe"
            resp = requests.get(url, timeout=5)
            if resp.status_code != 200:
                return render_template('result.html',
                    result={'success': False, 'message': '❌ توکن نامعتبر است!', 'logs': ''})
        except:
            return render_template('result.html',
                result={'success': False, 'message': '❌ خطا در بررسی توکن!', 'logs': ''})
        
        result = execute_user_bot(code_content, token, 1, is_permanent)
        return render_template('result.html', result=result)
    except Exception as e:
        return render_template('result.html',
            result={'success': False, 'message': f'❌ خطا: {str(e)}', 'logs': traceback.format_exc()})

@app.route('/health')
def health():
    return jsonify({'status': 'online', 'timestamp': time.time()})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    print("\n" + "="*60)
    print("🤖 سیستم مدیریت ربات‌ها")
    print("="*60)
    print(f"📡 پورت: {port}")
    print("🌐 آدرس: http://localhost:" + str(port))
    print("="*60 + "\n")
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
