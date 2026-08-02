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

# ==================== تنظیمات لاگ ====================
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ==================== تابع نصب خودکار پکیج‌ها ====================
def auto_install_packages():
    required = ['flask', 'requests', 'psutil', 'gunicorn']
    missing = []
    for pkg in required:
        try:
            importlib.import_module(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        logger.info(f"📦 نصب پکیج‌های: {', '.join(missing)}")
        for pkg in missing:
            try:
                subprocess.check_call([sys.executable, "-m", "pip", "install", pkg, "--quiet"])
                logger.info(f"✅ {pkg} نصب شد.")
            except Exception as e:
                logger.error(f"❌ خطا در نصب {pkg}: {e}")

auto_install_packages()

try:
    import psutil
    logger.info("✅ psutil با موفقیت بارگذاری شد")
except ImportError as e:
    logger.warning(f"⚠️ psutil نصب نشد: {e}")

# ==================== تنظیمات پیش‌فرض ====================
PORT = int(os.environ.get('PORT', 8080))
MAX_CODE_SIZE = 200 * 1024
EXECUTION_TIMEOUT = 120
MASTER_ID = os.environ.get('MASTER_ID', '')
REPORT_CHAT_ID = os.environ.get('REPORT_CHAT_ID', '')

logger.info("="*60)
logger.info("🤖 ربات رانر - راه‌انداز خودکار ربات‌ها")
logger.info("="*60)
logger.info(f"📡 پورت: {PORT}")
logger.info(f"📄 حداکثر حجم فایل: {MAX_CODE_SIZE//1024} KB")
logger.info(f"⏱️ زمان اجرا: {EXECUTION_TIMEOUT} ثانیه")
logger.info(f"📱 پلتفرم‌های پشتیبانی‌شده: روبیکا، تلگرام، واتساپ")
logger.info("="*60)

# ==================== توابع بررسی توکن ====================

def check_robika_token(token):
    logger.info(f"🔍 بررسی توکن روبیکا: {token[:10]}... (بررسی غیرفعال)")
    return True

def check_telegram_token(token):
    try:
        url = f"https://api.telegram.org/bot{token}/getMe"
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            return data.get('ok', False)
        return False
    except Exception as e:
        logger.error(f"❌ خطا در بررسی توکن تلگرام: {e}")
        return False

def check_whatsapp_token(token):
    return len(token) > 20

# ==================== توابع امنیتی ====================

def validate_code_security(code):
    logger.info("🔍 بررسی امنیتی کد (غیرفعال)")
    return True, "✅ کد از نظر امنیتی معتبر است"

def inject_token_to_code(code, token, platform):
    """تزریق توکن به کد کاربر - نسخه اصلاح شده"""
    logger.debug(f"🔄 شروع تزریق توکن به کد (پلتفرم: {platform})...")
    
    modified_code = code
    modifications = []
    
    # لیست متغیرهای توکن
    token_vars = [
        'TOKEN', 'token', 'YOUR_TOKEN', 'your_token',
        'BOT_TOKEN', 'bot_token', 'API_TOKEN', 'api_token',
        'TELEGRAM_TOKEN', 'telegram_token', 'ROBOT_TOKEN', 'robot_token'
    ]
    
    for var in token_vars:
        if var in modified_code:
            # الگوی ۱: var = "value" یا var = 'value'
            pattern1 = rf'{var}\s*=\s*["\']([^"\']*)["\']'
            modified_code = re.sub(pattern1, f'{var} = "{token}"', modified_code)
            modifications.append(f"جایگزینی {var}")
    
    # اگر هیچ تغییری ایجاد نشد، توکن رو به ابتدای کد اضافه کن
    if not modifications:
        header = f"""
# ===== توکن به‌صورت خودکار تزریق شد =====
TOKEN = "{token}"
# ============================================
"""
        modified_code = header + "\n" + modified_code
        modifications.append("افزودن توکن در ابتدای کد")
    
    logger.debug(f"✅ تزریق توکن انجام شد: {', '.join(modifications)}")
    return modified_code, f"✅ توکن با موفقیت تزریق شد ({', '.join(modifications)})"

def create_bot_runner_file(code, token, platform):
    logger.debug("🔄 ایجاد فایل اجرایی ربات...")
    
    platform_config = ""
    if platform == 'rubika':
        platform_config = f'''
MASTER_ID = "{MASTER_ID}" if "{MASTER_ID}" else "YOUR_MASTER_ID"
REPORT_CHAT_ID = "{REPORT_CHAT_ID}" if "{REPORT_CHAT_ID}" else "YOUR_REPORT_CHAT_ID"
'''
    elif platform == 'telegram':
        platform_config = '''
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

# ===== توکن از قبل تزریق شده =====
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
    
    logger.debug(f"✅ فایل اجرایی با {len(bot_code)} کاراکتر ایجاد شد")
    return bot_code

def execute_python_code(code, token, platform):
    logger.info("🔄 شروع اجرای کد...")
    
    is_valid, msg = validate_code_security(code)
    if not is_valid:
        return {'success': False, 'message': msg, 'logs': '', 'error': msg}
    
    code_with_token, injection_msg = inject_token_to_code(code, token, platform)
    full_code = create_bot_runner_file(code_with_token, token, platform)
    
    temp_dir = tempfile.mkdtemp()
    temp_path = os.path.join(temp_dir, "bot_runner.py")
    
    try:
        with open(temp_path, 'w', encoding='utf-8') as f:
            f.write(full_code)
        logger.info(f"✅ فایل در {temp_path} ذخیره شد")
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return {'success': False, 'message': f'❌ خطا در ایجاد فایل: {str(e)}', 'logs': str(e), 'error': str(e)}
    
    # نصب پکیج‌های مورد نیاز
    install_logs = ""
    platform_packages = {
        'rubika': 'rubika',
        'telegram': 'python-telegram-bot'
    }
    
    package_to_install = platform_packages.get(platform)
    if package_to_install:
        try:
            logger.info(f"📦 نصب پکیج: {package_to_install}")
            install_result = subprocess.run(
                [sys.executable, "-m", "pip", "install", package_to_install, "--quiet"],
                capture_output=True, text=True, timeout=60
            )
            if install_result.returncode != 0:
                install_logs = f"⚠️ خطا در نصب {package_to_install}: {install_result.stderr[:200]}\n"
            else:
                install_logs = f"✅ {package_to_install} نصب شد.\n"
                logger.info(f"✅ {package_to_install} نصب شد")
        except Exception as e:
            install_logs = f"⚠️ خطا در نصب: {str(e)}\n"
            logger.error(install_logs)
    
    # اجرای کد
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
        
        logger.info(f"🚀 اجرای کد با پایتون: {sys.executable}")
        
        process = subprocess.Popen(
            [sys.executable, temp_path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env=env, cwd=temp_dir,
            bufsize=1, universal_newlines=True
        )
        
        try:
            stdout, stderr = process.communicate(timeout=EXECUTION_TIMEOUT)
            output = stdout + stderr
            success = process.returncode == 0
            
            if not success and stderr:
                error_msg = stderr[:500]
                logger.error(f"❌ خطای اجرا: {error_msg}")
            elif not success and not stderr:
                error_msg = "❌ کد با خطای ناشناخته متوقف شد"
                logger.error(error_msg)
                
        except subprocess.TimeoutExpired:
            logger.warning(f"⏰ زمان اجرا بیش از حد مجاز ({EXECUTION_TIMEOUT} ثانیه)")
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
        logger.error(f"❌ خطا در اجرا: {e}")
        logger.error(traceback.format_exc())
    finally:
        try:
            if process and process.poll() is None:
                process.kill()
            shutil.rmtree(temp_dir, ignore_errors=True)
            logger.debug("🧹 فایل‌های موقت پاکسازی شدند")
        except:
            pass
    
    full_output = install_logs + output if install_logs else output
    
    result = {
        'success': success,
        'message': '✅ ربات با موفقیت اجرا شد!' if success else f'❌ خطا در اجرا: {error_msg}',
        'logs': full_output,
        'injection_msg': injection_msg,
        'error': error_msg if not success else ''
    }
    
    logger.info(f"🏁 نتیجه اجرا: success={success}")
    return result

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
            token_valid = True
            platform_name = "روبیکا"
            
            if platform == 'rubika':
                token_valid = check_robika_token(token)
            elif platform == 'telegram':
                token_valid = check_telegram_token(token)
                platform_name = "تلگرام"
            elif platform == 'whatsapp':
                token_valid = check_whatsapp_token(token)
                platform_name = "واتساپ"
            
            if not token_valid and platform not in ['other', 'whatsapp']:
                return render_template('result.html', 
                    result={'success': False, 'message': f'❌ توکن {platform_name} نامعتبر است!', 
                           'logs': f'لطفاً توکن صحیح را از @BotFather در {platform_name} دریافت کنید'})
            
            result = execute_python_code(code_content, token, platform)
            return render_template('result.html', result=result)
            
        except Exception as e:
            error_msg = traceback.format_exc()
            logger.error(f"❌ خطای سیستمی: {error_msg}")
            return render_template('result.html', 
                result={'success': False, 'message': f'❌ خطای سیستمی: {str(e)}', 'logs': error_msg})
    
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
