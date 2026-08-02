from flask import Flask, request, render_template, jsonify, send_file
import subprocess
import tempfile
import os
import requests
import json
import time
import sys
import re
import shutil
import signal
import psutil

app = Flask(__name__)

# ==================== تنظیمات ====================
MAX_CODE_SIZE = 100 * 1024  # 100 کیلوبایت
EXECUTION_TIMEOUT = 60  # 60 ثانیه
PORT = int(os.environ.get('PORT', 8080))

# ==================== توابع کمکی ====================

def check_robika_token(token):
    """بررسی اعتبار توکن روبیکا"""
    try:
        url = f"https://api.rubika.ir/bot/v1/getMe?token={token}"
        headers = {"Content-Type": "application/json"}
        resp = requests.get(url, headers=headers, timeout=5)
        
        if resp.status_code == 200:
            data = resp.json()
            return data.get('ok', False)
        return False
    except Exception as e:
        print(f"خطا در بررسی توکن روبیکا: {e}")
        return False

def check_telegram_token(token):
    """بررسی اعتبار توکن تلگرام"""
    try:
        url = f"https://api.telegram.org/bot{token}/getMe"
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            return data.get('ok', False)
        return False
    except Exception as e:
        print(f"خطا در بررسی توکن تلگرام: {e}")
        return False

def validate_code_security(code):
    """بررسی امنیتی کد - جلوگیری از کدهای مخرب"""
    dangerous_patterns = [
        (r'os\.system', 'استفاده از os.system ممنوع است'),
        (r'subprocess\.', 'استفاده از subprocess ممنوع است'),
        (r'\bexec\s*\(', 'استفاده از exec ممنوع است'),
        (r'\beval\s*\(', 'استفاده از eval ممنوع است'),
        (r'__import__\s*\(', 'استفاده از __import__ ممنوع است'),
        (r'open\s*\([^)]*[\'"]w', 'نوشتن در فایل‌ها ممنوع است'),
        (r'shutil\.', 'استفاده از shutil ممنوع است'),
        (r'os\.remove', 'حذف فایل‌ها ممنوع است'),
        (r'os\.rmdir', 'حذف دایرکتوری ممنوع است'),
        (r'socket\.', 'استفاده از socket ممنوع است'),
        (r'urllib\.', 'استفاده از urllib ممنوع است'),
    ]
    
    for pattern, message in dangerous_patterns:
        if re.search(pattern, code, re.IGNORECASE):
            return False, f"⚠️ {message}"
    
    return True, "✅ کد از نظر امنیتی معتبر است"

def inject_token_to_code(code, token, platform):
    """تزریق توکن به کد کاربر با روش‌های مختلف"""
    modifications = []
    modified_code = code
    
    # روش ۱: جایگزینی مستقیم متغیرهای توکن
    token_vars = [
        'YOUR_TOKEN', 'your_token', 'TOKEN', 'token', 
        'BOT_TOKEN', 'bot_token', 'API_TOKEN', 'api_token',
        'TELEGRAM_TOKEN', 'telegram_token'
    ]
    
    for var in token_vars:
        if var in modified_code:
            # اگر قبلاً به صورت رشته تعریف شده بود
            modified_code = re.sub(rf'{var}\s*=\s*["\']([^"\']*)["\']', f'{var} = "{token}"', modified_code)
            # اگر فقط اسم متغیر بود
            modified_code = modified_code.replace(var, f'"{token}"')
            modifications.append(f"جایگزینی {var}")
    
    # روش ۲: اضافه کردن در ابتدای کد (اگر هیچ تغییری نشد)
    if not modifications:
        header = f"""
# ===== توکن به‌صورت خودکار تزریق شد =====
TOKEN = "{token}"
# ============================================
"""
        modified_code = header + "\n" + modified_code
        modifications.append("افزودن توکن در ابتدای کد")
    
    if not modifications:
        return code, "⚠️ هیچ تغییری در کد اعمال نشد. لطفاً از متغیر TOKEN استفاده کنید."
    
    return modified_code, f"✅ توکن با موفقیت تزریق شد ({', '.join(modifications)})"

def create_bot_runner_file(code, token, platform):
    """ایجاد فایل اجرایی ربات با کد کاربر"""
    
    # ایجاد کد کامل برای اجرا
    bot_code = f'''# -*- coding: utf-8 -*-
import os
import sys
import asyncio
import logging
import json
import time
import sqlite3
from datetime import datetime

# ===== تنظیم توکن از محیط =====
os.environ["TOKEN"] = "{token}"

# ===== کد کاربر =====
{code}

# ===== اجرا =====
if __name__ == "__main__":
    try:
        # پیدا کردن تابع main یا اجرای مستقیم
        if 'main' in dir():
            asyncio.run(main())
        else:
            # اگر تابع main وجود نداشت، اجرای مستقیم
            pass
    except KeyboardInterrupt:
        print("🛑 ربات متوقف شد")
    except Exception as e:
        print(f"❌ خطا در اجرا: {{e}}")
        import traceback
        traceback.print_exc()
'''
    
    return bot_code

def execute_python_code(code, token, platform):
    """اجرای کد پایتون در محیط ایزوله با مدیریت کامل"""
    
    # ۱. اعتبارسنجی امنیتی
    is_valid, msg = validate_code_security(code)
    if not is_valid:
        return {
            'success': False,
            'message': msg,
            'logs': '',
            'error': msg
        }
    
    # ۲. تزریق توکن
    code_with_token, injection_msg = inject_token_to_code(code, token, platform)
    
    # ۳. ایجاد فایل کامل ربات
    full_code = create_bot_runner_file(code_with_token, token, platform)
    
    # ۴. ذخیره در فایل موقت
    temp_dir = tempfile.mkdtemp()
    temp_path = os.path.join(temp_dir, "bot_runner.py")
    
    try:
        with open(temp_path, 'w', encoding='utf-8') as f:
            f.write(full_code)
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return {
            'success': False,
            'message': f'❌ خطا در ایجاد فایل: {str(e)}',
            'logs': str(e),
            'error': str(e)
        }
    
    # ۵. نصب وابستگی‌ها (اگر نیاز باشد)
    install_logs = ""
    try:
        # نصب rubika
        install_result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "rubika", "--quiet"],
            capture_output=True,
            text=True,
            timeout=30
        )
        if install_result.returncode != 0:
            install_logs = f"⚠️ خطا در نصب rubika: {install_result.stderr}\n"
    except Exception as e:
        install_logs = f"⚠️ خطا در نصب: {str(e)}\n"
    
    # ۶. اجرای کد در محیط ایزوله
    output = ""
    success = False
    error_msg = ""
    process = None
    
    try:
        # تنظیم محیط اجرا
        env = os.environ.copy()
        env["TOKEN"] = token
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        
        # اجرا با Popen برای مدیریت بهتر
        process = subprocess.Popen(
            [sys.executable, temp_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            cwd=temp_dir,
            bufsize=1,
            universal_newlines=True
        )
        
        try:
            # گرفتن خروجی با تایم‌اوت
            stdout, stderr = process.communicate(timeout=EXECUTION_TIMEOUT)
            output = stdout + stderr
            success = process.returncode == 0
            
            if not success and stderr:
                error_msg = stderr
            elif not success and not stderr:
                error_msg = "❌ کد با خطای ناشناخته متوقف شد"
            
        except subprocess.TimeoutExpired:
            # kill process and children
            try:
                parent = psutil.Process(process.pid)
                children = parent.children(recursive=True)
                for child in children:
                    child.kill()
                parent.kill()
                psutil.wait_procs(children, timeout=5)
            except:
                process.kill()
            
            output = f"⏰ زمان اجرا بیش از حد مجاز ({EXECUTION_TIMEOUT} ثانیه)\n"
            success = False
            error_msg = "Timeout"
            
    except FileNotFoundError:
        output = "❌ پایتون روی سرور نصب نیست!"
        success = False
        error_msg = "Python not found"
    except Exception as e:
        output = f"❌ خطا در اجرا: {str(e)}"
        success = False
        error_msg = str(e)
    finally:
        # پاکسازی فایل‌های موقت
        try:
            if process and process.poll() is None:
                process.kill()
            shutil.rmtree(temp_dir, ignore_errors=True)
        except:
            pass
    
    # ۷. آماده‌سازی نتیجه
    full_output = install_logs + output if install_logs else output
    
    return {
        'success': success,
        'message': '✅ ربات با موفقیت اجرا شد!' if success else f'❌ خطا در اجرا: {error_msg}',
        'logs': full_output,
        'injection_msg': injection_msg,
        'error': error_msg if not success else ''
    }

# ==================== مسیرهای سایت ====================

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        try:
            platform = request.form.get('platform', 'rubika')
            code_type = request.form.get('code_type', 'file')
            token = request.form.get('token', '').strip()
            
            # اعتبارسنجی توکن
            if not token:
                return render_template('result.html', 
                    result={'success': False, 'message': '❌ لطفاً توکن را وارد کنید', 'logs': ''})
            
            # دریافت کد
            if code_type == 'file':
                if 'code_file' not in request.files:
                    return render_template('result.html', 
                        result={'success': False, 'message': '❌ لطفاً یک فایل انتخاب کنید', 'logs': ''})
                
                file = request.files['code_file']
                if file.filename == '':
                    return render_template('result.html', 
                        result={'success': False, 'message': '❌ لطفاً یک فایل انتخاب کنید', 'logs': ''})
                
                # بررسی حجم فایل
                file_content = file.read()
                if len(file_content) > MAX_CODE_SIZE:
                    return render_template('result.html', 
                        result={'success': False, 'message': f'❌ حجم فایل بیشتر از {MAX_CODE_SIZE//1024} کیلوبایت است', 'logs': ''})
                
                try:
                    code_content = file_content.decode('utf-8', errors='ignore')
                except:
                    return render_template('result.html', 
                        result={'success': False, 'message': '❌ فایل معتبر نیست (فقط UTF-8 پشتیبانی می‌شود)', 'logs': ''})
                
            else:  # text
                code_content = request.form.get('code_text', '').strip()
                if not code_content:
                    return render_template('result.html', 
                        result={'success': False, 'message': '❌ لطفاً کد را وارد کنید', 'logs': ''})
            
            # بررسی اعتبار توکن (قبل از اجرا)
            token_valid = False
            if platform == 'rubika':
                token_valid = check_robika_token(token)
            elif platform == 'telegram':
                token_valid = check_telegram_token(token)
            elif platform == 'other':
                token_valid = True
            
            if not token_valid and platform != 'other':
                return render_template('result.html', 
                    result={'success': False, 'message': '❌ توکن نامعتبر است!', 
                           'logs': 'لطفاً توکن صحیح را از @BotFather دریافت کنید'})
            
            # اجرای کد
            result = execute_python_code(code_content, token, platform)
            
            return render_template('result.html', result=result)
            
        except Exception as e:
            import traceback
            return render_template('result.html', 
                result={'success': False, 'message': f'❌ خطای سیستمی: {str(e)}', 
                       'logs': traceback.format_exc()})
    
    return render_template('index.html')

@app.route('/health', methods=['GET'])
def health():
    """بررسی وضعیت سرور"""
    return jsonify({
        'status': 'online',
        'python': sys.version,
        'timestamp': time.time(),
        'port': PORT
    })

@app.errorhandler(404)
def not_found(e):
    return render_template('result.html', 
        result={'success': False, 'message': '❌ صفحه مورد نظر یافت نشد', 'logs': ''}), 404

@app.errorhandler(500)
def server_error(e):
    return render_template('result.html', 
        result={'success': False, 'message': '❌ خطای داخلی سرور', 'logs': str(e)}), 500

if __name__ == '__main__':
    print(f"🚀 ربات رانر در حال اجرا روی پورت {PORT}")
    print(f"📡 آدرس: http://localhost:{PORT}")
    print(f"🔗 دامنه: http://your-domain.com:{PORT}")
    app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True)
