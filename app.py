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
MAX_CODE_SIZE = 500 * 1024
EXECUTION_TIMEOUT = 999999

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============================================================
# ===== تابع بررسی توکن =====
# ============================================================

def check_telegram_token(token):
    """بررسی اعتبار توکن تلگرام"""
    try:
        url = f"https://api.telegram.org/bot{token}/getMe"
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200:
            return resp.json().get('ok', False)
        return False
    except Exception as e:
        logger.error(f"❌ خطا در بررسی توکن: {e}")
        return False

# ============================================================
# ===== تابع اصلی: اجرای کد کاربر با توکن خودش =====
# ============================================================

def execute_user_bot(user_code, user_token):
    """
    این تابع کد کاربر رو میگیره، توکنش رو توش جایگذاری میکنه و اجراش میکنه
    """
    logger.info(f"🔄 شروع اجرای ربات کاربر با توکن: {user_token[:10]}...")
    
    # ===== مرحله ۱: تزریق توکن کاربر به کد خودش =====
    # جایگزینی تمام متغیرهای احتمالی توکن با توکن کاربر
    token_vars = ['TOKEN', 'token', 'YOUR_TOKEN', 'your_token', 'BOT_TOKEN', 'bot_token']
    
    modified_code = user_code
    for var in token_vars:
        # جایگزینی TOKEN = "..." با توکن جدید
        modified_code = re.sub(rf'{var}\s*=\s*["\']([^"\']*)["\']', f'{var} = "{user_token}"', modified_code)
        # جایگزینی TOKEN = '...' با توکن جدید
        modified_code = re.sub(rf'{var}\s*=\s*\'([^\']*)\'', f'{var} = "{user_token}"', modified_code)
    
    # اگر هیچ تغییری ایجاد نشد، توکن رو به ابتدای کد اضافه کن
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
    
    # ===== مرحله ۳: نصب وابستگی‌های احتمالی =====
    install_logs = ""
    
    # بررسی وابستگی‌های رایج
    dependencies = []
    if 'import telegram' in modified_code or 'from telegram' in modified_code:
        dependencies.append('python-telegram-bot')
    if 'import rubika' in modified_code or 'from rubika' in modified_code:
        dependencies.append('rubika')
    if 'import requests' in modified_code:
        dependencies.append('requests')
    
    for dep in dependencies:
        try:
            logger.info(f"📦 نصب وابستگی: {dep}")
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", dep, "--quiet"],
                capture_output=True, text=True, timeout=60
            )
            if result.returncode == 0:
                install_logs += f"✅ {dep} نصب شد.\n"
            else:
                install_logs += f"⚠️ خطا در نصب {dep}\n"
        except Exception as e:
            install_logs += f"⚠️ خطا در نصب {dep}: {str(e)}\n"
    
    # ===== مرحله ۴: اجرای کد کاربر =====
    output = ""
    success = False
    error_msg = ""
    process = None
    
    try:
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["TOKEN"] = user_token
        
        logger.info(f"🚀 اجرای کد کاربر...")
        
        process = subprocess.Popen(
            [sys.executable, temp_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            cwd=temp_dir
        )
        
        # اجرا بدون محدودیت زمانی (تا زمانی که ربات روشنه)
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
            # فایل‌های موقت رو پاک نکنید تا ربات به کار خودش ادامه بده
            # shutil.rmtree(temp_dir, ignore_errors=True)
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
            
            # اجرای کد کاربر با توکن خودش
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
    print("🤖 ربات رانر - اجرای کد کاربر با توکن خودش")
    print("="*60)
    print(f"📡 پورت: {port}")
    print("🌐 آدرس: http://localhost:" + str(port))
    print("💡 کاربر کد و توکن خودش رو قرار میده و ربات خودش اجرا میشه!")
    print("="*60 + "\n")
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
