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
from datetime import datetime
import threading
import signal

app = Flask(__name__)

PORT = int(os.environ.get('PORT', 8080))
MAX_CODE_SIZE = 500 * 1024  # 500 کیلوبایت
# ===== بدون محدودیت زمانی =====
EXECUTION_TIMEOUT = None  # None یعنی نامحدود

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============================================================
# ===== تابع بررسی توکن =====
# ============================================================

def check_telegram_token(token):
    """بررسی اعتبار توکن تلگرام"""
    try:
        url = f"https://api.telegram.org/bot{token}/getMe"
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            return resp.json().get('ok', False)
        return False
    except Exception as e:
        logger.error(f"❌ خطا در بررسی توکن: {e}")
        return False

# ============================================================
# ===== تابع تشخیص و نصب همه وابستگی‌ها =====
# ============================================================

def detect_and_install_dependencies(code):
    """تشخیص و نصب خودکار همه وابستگی‌های مورد نیاز کد"""
    install_logs = ""
    
    # ===== لیست کامل وابستگی‌های تلگرام و پکیج‌های مرتبط =====
    all_deps = {
        # کتابخانه‌های اصلی تلگرام
        'telebot': 'pyTelegramBotAPI',
        'pyTelegramBotAPI': 'pyTelegramBotAPI',
        'telegram': 'python-telegram-bot',
        'python-telegram-bot': 'python-telegram-bot',
        
        # کتابخانه‌های روبیکا
        'rubika': 'rubika',
        'rubka': 'rubika',
        
        # کتابخانه‌های عمومی
        'requests': 'requests',
        'jdatetime': 'jdatetime',
        'persiantools': 'persiantools',
        'python-dotenv': 'python-dotenv',
        'sqlalchemy': 'sqlalchemy',
        'redis': 'redis',
        'pymongo': 'pymongo',
        'mysql-connector-python': 'mysql-connector-python',
        'psycopg2': 'psycopg2-binary',
        'aiohttp': 'aiohttp',
        'websockets': 'websockets',
        'flask': 'flask',
        'django': 'django',
        'fastapi': 'fastapi',
        'uvicorn': 'uvicorn',
        
        # کتابخانه‌های پردازش
        'pandas': 'pandas',
        'numpy': 'numpy',
        'matplotlib': 'matplotlib',
        'pillow': 'pillow',
        'opencv': 'opencv-python',
        
        # کتابخانه‌های امنیتی
        'cryptography': 'cryptography',
        'pycryptodome': 'pycryptodome',
        'jwt': 'pyjwt',
        
        # کتابخانه‌های کاربردی
        'beautifulsoup4': 'beautifulsoup4',
        'selenium': 'selenium',
        'scrapy': 'scrapy',
        'lxml': 'lxml',
    }
    
    # ===== تشخیص پکیج‌های مورد نیاز از کد =====
    deps_to_install = []
    code_lower = code.lower()
    
    for keyword, package in all_deps.items():
        if package and keyword in code_lower:
            # بررسی اینکه پکیج نصب هست یا نه
            try:
                importlib.import_module(package.replace('-', '_'))
            except ImportError:
                if package not in deps_to_install:
                    deps_to_install.append(package)
    
    # ===== نصب پکیج‌ها =====
    for dep in deps_to_install:
        try:
            logger.info(f"📦 نصب وابستگی: {dep}")
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", dep, "--quiet"],
                capture_output=True, text=True, timeout=120
            )
            if result.returncode == 0:
                install_logs += f"✅ {dep} نصب شد.\n"
                logger.info(f"✅ {dep} نصب شد")
            else:
                install_logs += f"⚠️ خطا در نصب {dep}: {result.stderr[:100]}\n"
                logger.warning(f"⚠️ خطا در نصب {dep}")
        except Exception as e:
            install_logs += f"⚠️ خطا در نصب {dep}: {str(e)}\n"
            logger.error(f"❌ خطا در نصب {dep}: {e}")
    
    return install_logs

# ============================================================
# ===== تابع اصلی: اجرای کد کاربر با توکن خودش (نامحدود) =====
# ============================================================

def execute_user_bot(user_code, user_token):
    """
    این تابع کد کاربر رو میگیره، توکنش رو توش جایگذاری میکنه و بدون محدودیت زمانی اجراش میکنه
    """
    logger.info(f"🔄 شروع اجرای ربات کاربر با توکن: {user_token[:10]}...")
    
    # ===== مرحله ۱: تزریق توکن کاربر به کد خودش =====
    token_vars = ['TOKEN', 'token', 'YOUR_TOKEN', 'your_token', 'BOT_TOKEN', 'bot_token', 'API_TOKEN', 'api_token']
    
    modified_code = user_code
    for var in token_vars:
        modified_code = re.sub(rf'{var}\s*=\s*["\']([^"\']*)["\']', f'{var} = "{user_token}"', modified_code)
        modified_code = re.sub(rf'{var}\s*=\s*\'([^\']*)\'', f'{var} = "{user_token}"', modified_code)
    
    if 'TOKEN' not in modified_code and 'token' not in modified_code:
        modified_code = f'TOKEN = "{user_token}"\n\n' + modified_code
    
    # ===== مرحله ۲: ذخیره کد در فایل موقت =====
    temp_dir = tempfile.mkdtemp()
    temp_path = os.path.join(temp_dir, "user_bot.py")
    
    try:
        with open(temp_path, 'w', encoding='utf-8') as f:
            f.write(modified_code)
        logger.info(f"✅ کد کاربر در {temp_path} ذخیره شد")
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return {'success': False, 'message': f'❌ خطا در ذخیره کد: {str(e)}', 'logs': str(e)}
    
    # ===== مرحله ۳: نصب وابستگی‌ها =====
    install_logs = detect_and_install_dependencies(modified_code)
    
    # ===== مرحله ۴: اجرای کد کاربر (بدون محدودیت زمانی) =====
    output = ""
    success = False
    error_msg = ""
    process = None
    
    try:
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["TOKEN"] = user_token
        
        logger.info(f"🚀 اجرای کد کاربر (بدون محدودیت زمانی)...")
        
        process = subprocess.Popen(
            [sys.executable, temp_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            cwd=temp_dir
        )
        
        # ===== بدون timeout =====
        stdout, stderr = process.communicate()
        output = stdout + stderr
        success = process.returncode == 0
        
        if not success and stderr:
            error_msg = stderr[:500]
            logger.error(f"❌ خطای اجرا: {error_msg}")
            
    except Exception as e:
        output = f"❌ خطا در اجرا: {str(e)}"
        success = False
        error_msg = str(e)
        logger.error(f"❌ خطا در اجرا: {e}")
        
    finally:
        try:
            if process and process.poll() is None:
                process.kill()
            # فایل‌های موقت رو پاک نمیکنیم تا ربات به کار خودش ادامه بده
        except:
            pass
    
    full_output = install_logs + output if install_logs else output
    
    return {
        'success': success,
        'message': '✅ ربات شما با موفقیت اجرا شد!' if success else f'❌ خطا در اجرا: {error_msg}',
        'logs': full_output,
        'error': error_msg if not success else ''
    }

# ============================================================
# ===== مسیرهای سایت =====
# ============================================================

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        try:
            token = request.form.get('token', '').strip()
            code_type = request.form.get('code_type', 'file')
            
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
            
            # بررسی توکن
            if not check_telegram_token(token):
                return render_template('result.html', 
                    result={'success': False, 'message': '❌ توکن تلگرام نامعتبر است!', 
                           'logs': 'لطفاً توکن صحیح را از @BotFather دریافت کنید'})
            
            # اجرای کد کاربر با توکن خودش (نامحدود)
            result = execute_user_bot(code_content, token)
            return render_template('result.html', result=result)
            
        except Exception as e:
            return render_template('result.html', 
                result={'success': False, 'message': f'❌ خطای سیستمی: {str(e)}', 
                       'logs': traceback.format_exc()})
    
    return render_template('index.html')

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'online', 'python': sys.version, 'timestamp': time.time()})

@app.errorhandler(404)
def not_found(e):
    return render_template('result.html', 
        result={'success': False, 'message': '❌ صفحه مورد نظر یافت نشد', 'logs': ''}), 404

@app.errorhandler(500)
def server_error(e):
    return render_template('result.html', 
        result={'success': False, 'message': '❌ خطای داخلی سرور', 'logs': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    print("\n" + "="*60)
    print("🤖 ربات رانر - اجرای کد کاربر با توکن خودش (نامحدود)")
    print("="*60)
    print(f"📡 پورت: {port}")
    print("🌐 آدرس: http://localhost:" + str(port))
    print("⏱️ زمان اجرا: ♾️ نامحدود")
    print("📦 پشتیبانی از همه وابستگی‌های تلگرام")
    print("="*60 + "\n")
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
