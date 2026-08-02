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

# ==================== تنظیمات لاگ ====================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__)

# ==================== تابع نصب خودکار پکیج‌ها ====================
def auto_install_packages():
    """نصب خودکار پکیج‌های مورد نیاز در صورت نبودن"""
    required = ['flask', 'requests', 'psutil', 'gunicorn']
    missing = []
    
    for pkg in required:
        try:
            importlib.import_module(pkg)
        except ImportError:
            missing.append(pkg)
    
    if missing:
        print(f"📦 نصب پکیج‌های: {', '.join(missing)}")
        for pkg in missing:
            try:
                subprocess.check_call([sys.executable, "-m", "pip", "install", pkg, "--quiet"])
                print(f"✅ {pkg} نصب شد.")
            except Exception as e:
                print(f"❌ خطا در نصب {pkg}: {e}")

auto_install_packages()

# ==================== ایمپورت پکیج‌ها بعد از نصب ====================
try:
    import psutil
except ImportError:
    print("⚠️ psutil نصب نشد. برخی قابلیت‌ها محدود می‌شوند.")

# ==================== تنظیمات پیش‌فرض (بدون نیاز به input) ====================
PORT = int(os.environ.get('PORT', 8080))
MAX_CODE_SIZE = 100 * 1024  # 100 کیلوبایت
EXECUTION_TIMEOUT = 60  # 60 ثانیه
MASTER_ID = os.environ.get('MASTER_ID', '')
REPORT_CHAT_ID = os.environ.get('REPORT_CHAT_ID', '')

# ==================== تنظیمات ====================
print(f"\n🚀 تنظیمات اعمال شد:")
print(f"   📡 پورت: {PORT}")
print(f"   📄 حداکثر حجم: {MAX_CODE_SIZE//1024} KB")
print(f"   ⏱️ زمان اجرا: {EXECUTION_TIMEOUT} ثانیه")
if MASTER_ID:
    print(f"   👑 ارباب: {MASTER_ID}")
if REPORT_CHAT_ID:
    print(f"   📨 گپ گزارش: {REPORT_CHAT_ID}")
print("")

# ==================== توابع بررسی توکن ====================

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
        logging.error(f"خطا در بررسی توکن روبیکا: {e}")
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
        logging.error(f"خطا در بررسی توکن تلگرام: {e}")
        return False

def check_whatsapp_token(token):
    """بررسی اعتبار توکن واتساپ (ساده)"""
    return len(token) > 20

# ==================== توابع امنیتی ====================

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
        (r'requests\.(get|post|put|delete)', 'درخواست به بیرون ممنوع است'),
    ]
    
    for pattern, message in dangerous_patterns:
        if re.search(pattern, code, re.IGNORECASE):
            return False, f"⚠️ {message}"
    
    return True, "✅ کد از نظر امنیتی معتبر است"

def inject_token_to_code(code, token, platform):
    """تزریق توکن به کد کاربر با روش‌های مختلف"""
    modifications = []
    modified_code = code
    
    token_vars = [
        'YOUR_TOKEN', 'your_token', 'TOKEN', 'token', 
        'BOT_TOKEN', 'bot_token', 'API_TOKEN', 'api_token',
        'TELEGRAM_TOKEN', 'telegram_token',
        'ROBOT_TOKEN', 'robot_token'
    ]
    
    for var in token_vars:
        if var in modified_code:
            modified_code = re.sub(rf'{var}\s*=\s*["\']([^"\']*)["\']', f'{var} = "{token}"', modified_code)
            modified_code = re.sub(rf'\b{var}\b', f'"{token}"', modified_code)
            modifications.append(f"جایگزینی {var}")
    
    if not modifications:
        header = f"""
# ===== توکن به‌صورت خودکار تزریق شد =====
TOKEN = "{token}"
# ============================================
"""
        modified_code = header + "\n" + modified_code
        modifications.append("افزودن توکن در ابتدای کد")
    
    return modified_code, f"✅ توکن با موفقیت تزریق شد ({', '.join(modifications)})"

# ==================== ایجاد فایل اجرایی ربات ====================

def create_bot_runner_file(code, token, platform):
    """ایجاد فایل کامل ربات با کد کاربر و تنظیمات"""
    
    platform_config = ""
    if platform == 'rubika':
        platform_config = f'''
# تنظیمات روبیکا
MASTER_ID = "{MASTER_ID}" if "{MASTER_ID}" else "YOUR_MASTER_ID"
REPORT_CHAT_ID = "{REPORT_CHAT_ID}" if "{REPORT_CHAT_ID}" else "YOUR_REPORT_CHAT_ID"
'''
    elif platform == 'telegram':
        platform_config = '''
# تنظیمات تلگرام
MASTER_ID = "YOUR_MASTER_ID"
REPORT_CHAT_ID = "YOUR_REPORT_CHAT_ID"
'''
    
    bot_code = f'''# -*- coding: utf-8 -*-
import os
import sys
import asyncio
import logging
import json
import time
import sqlite3
from datetime import datetime

# ===== تنظیم توکن =====
TOKEN = "{token}"
os.environ["TOKEN"] = TOKEN

# ===== تنظیمات پلتفرم =====
{platform_config}

# ===== کد کاربر =====
{code}

# ===== اجرا =====
if __name__ == "__main__":
    try:
        if 'main' in dir():
            asyncio.run(main())
        else:
            print("⚠️ تابع main پیدا نشد. اجرای مستقیم...")
    except KeyboardInterrupt:
        print("🛑 ربات متوقف شد")
    except Exception as e:
        print(f"❌ خطا در اجرا: {{e}}")
        import traceback
        traceback.print_exc()
'''
    
    return bot_code

# ==================== اجرای کد ====================

def execute_python_code(code, token, platform):
    """اجرای کد پایتون در محیط ایزوله با مدیریت کامل"""
    
    is_valid, msg = validate_code_security(code)
    if not is_valid:
        return {
            'success': False,
            'message': msg,
            'logs': '',
            'error': msg
        }
    
    code_with_token, injection_msg = inject_token_to_code(code, token, platform)
    full_code = create_bot_runner_file(code_with_token, token, platform)
    
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
    
    install_logs = ""
    platform_packages = {
        'rubika': 'rubika',
        'telegram': 'python-telegram-bot'
    }
    
    package_to_install = platform_packages.get(platform)
    if package_to_install:
        try:
            install_result = subprocess.run(
                [sys.executable, "-m", "pip", "install", package_to_install, "--quiet"],
                capture_output=True,
                text=True,
                timeout=30
            )
            if install_result.returncode != 0:
                install_logs = f"⚠️ خطا در نصب {package_to_install}: {install_result.stderr[:200]}\n"
            else:
                install_logs = f"✅ {package_to_install} نصب شد.\n"
        except Exception as e:
            install_logs = f"⚠️ خطا در نصب: {str(e)}\n"
    
    output = ""
    success = False
    error_msg = ""
    process = None
    
    try:
        env = os.environ.copy()
        env["TOKEN"] = token
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        if MASTER_ID:
            env["MASTER_ID"] = MASTER_ID
        if REPORT_CHAT_ID:
            env["REPORT_CHAT_ID"] = REPORT_CHAT_ID
        
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
            stdout, stderr = process.communicate(timeout=EXECUTION_TIMEOUT)
            output = stdout + stderr
            success = process.returncode == 0
            
            if not success and stderr:
                error_msg = stderr[:500]
            elif not success and not stderr:
                error_msg = "❌ کد با خطای ناشناخته متوقف شد"
            
        except subprocess.TimeoutExpired:
            try:
                if 'psutil' in sys.modules:
                    parent = psutil.Process(process.pid)
                    children = parent.children(recursive=True)
                    for child in children:
                        child.kill()
                    parent.kill()
                else:
                    process.kill()
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
        try:
            if process and process.poll() is None:
                process.kill()
            shutil.rmtree(temp_dir, ignore_errors=True)
        except:
            pass
    
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
            
            if not token:
                return render_template('result.html', 
                    result={'success': False, 'message': '❌ لطفاً توکن را وارد کنید', 'logs': ''})
            
            if code_type == 'file':
                if 'code_file' not in request.files:
                    return render_template('result.html', 
                        result={'success': False, 'message': '❌ لطفاً یک فایل انتخاب کنید', 'logs': ''})
                
                file = request.files['code_file']
                if file.filename == '':
                    return render_template('result.html', 
                        result={'success': False, 'message': '❌ لطفاً یک فایل انتخاب کنید', 'logs': ''})
                
                file_content = file.read()
                if len(file_content) > MAX_CODE_SIZE:
                    return render_template('result.html', 
                        result={'success': False, 'message': f'❌ حجم فایل بیشتر از {MAX_CODE_SIZE//1024} کیلوبایت است', 'logs': ''})
                
                try:
                    code_content = file_content.decode('utf-8', errors='ignore')
                except:
                    return render_template('result.html', 
                        result={'success': False, 'message': '❌ فایل معتبر نیست (فقط UTF-8 پشتیبانی می‌شود)', 'logs': ''})
                
            else:
                code_content = request.form.get('code_text', '').strip()
                if not code_content:
                    return render_template('result.html', 
                        result={'success': False, 'message': '❌ لطفاً کد را وارد کنید', 'logs': ''})
            
            token_valid = False
            platform_name = ""
            
            if platform == 'rubika':
                token_valid = check_robika_token(token)
                platform_name = "روبیکا"
            elif platform == 'telegram':
                token_valid = check_telegram_token(token)
                platform_name = "تلگرام"
            elif platform == 'whatsapp':
                token_valid = check_whatsapp_token(token)
                platform_name = "واتساپ"
            else:
                token_valid = True
                platform_name = "سایر"
            
            if not token_valid and platform not in ['other', 'whatsapp']:
                return render_template('result.html', 
                    result={'success': False, 'message': f'❌ توکن {platform_name} نامعتبر است!', 
                           'logs': f'لطفاً توکن صحیح را از @BotFather در {platform_name} دریافت کنید'})
            
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
    return jsonify({
        'status': 'online',
        'python': sys.version,
        'platforms': ['rubika', 'telegram', 'whatsapp'],
        'config': {
            'port': PORT,
            'max_size': MAX_CODE_SIZE // 1024,
            'timeout': EXECUTION_TIMEOUT
        },
        'timestamp': time.time()
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
    print("\n" + "="*60)
    print("🤖 ربات رانر - راه‌انداز خودکار ربات‌ها")
    print("="*60)
    print(f"📡 پورت: {PORT}")
    print(f"📄 حداکثر حجم فایل: {MAX_CODE_SIZE//1024} KB")
    print(f"⏱️ زمان اجرا: {EXECUTION_TIMEOUT} ثانیه")
    print(f"📱 پلتفرم‌های پشتیبانی‌شده: روبیکا، تلگرام، واتساپ")
    print("="*60)
    print("🌐 آدرس: http://localhost:" + str(PORT))
    print("="*60 + "\n")
    
    app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True)
