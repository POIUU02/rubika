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

# ==================== تنظیمات لاگ پیشرفته ====================
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

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
        logger.info(f"📦 نصب پکیج‌های: {', '.join(missing)}")
        for pkg in missing:
            try:
                subprocess.check_call([sys.executable, "-m", "pip", "install", pkg, "--quiet"])
                logger.info(f"✅ {pkg} نصب شد.")
            except Exception as e:
                logger.error(f"❌ خطا در نصب {pkg}: {e}")

auto_install_packages()

# ==================== ایمپورت پکیج‌ها بعد از نصب ====================
try:
    import psutil
    logger.info("✅ psutil با موفقیت بارگذاری شد")
except ImportError as e:
    logger.warning(f"⚠️ psutil نصب نشد: {e}")

# ==================== تنظیمات پیش‌فرض ====================
PORT = int(os.environ.get('PORT', 8080))
MAX_CODE_SIZE = 100 * 1024  # 100 کیلوبایت
EXECUTION_TIMEOUT = 60  # 60 ثانیه
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
    """بررسی اعتبار توکن روبیکا"""
    logger.debug(f"🔍 بررسی توکن روبیکا: {token[:10]}...")
    try:
        url = f"https://api.rubika.ir/bot/v1/getMe?token={token}"
        headers = {"Content-Type": "application/json"}
        resp = requests.get(url, headers=headers, timeout=5)
        
        logger.debug(f"📡 پاسخ روبیکا: status={resp.status_code}")
        
        if resp.status_code == 200:
            data = resp.json()
            result = data.get('ok', False)
            logger.debug(f"✅ نتیجه بررسی توکن: {result}")
            return result
        return False
    except Exception as e:
        logger.error(f"❌ خطا در بررسی توکن روبیکا: {e}")
        logger.error(traceback.format_exc())
        return False

def check_telegram_token(token):
    """بررسی اعتبار توکن تلگرام"""
    logger.debug(f"🔍 بررسی توکن تلگرام: {token[:10]}...")
    try:
        url = f"https://api.telegram.org/bot{token}/getMe"
        resp = requests.get(url, timeout=5)
        
        logger.debug(f"📡 پاسخ تلگرام: status={resp.status_code}")
        
        if resp.status_code == 200:
            data = resp.json()
            result = data.get('ok', False)
            logger.debug(f"✅ نتیجه بررسی توکن: {result}")
            return result
        return False
    except Exception as e:
        logger.error(f"❌ خطا در بررسی توکن تلگرام: {e}")
        logger.error(traceback.format_exc())
        return False

def check_whatsapp_token(token):
    """بررسی اعتبار توکن واتساپ (ساده)"""
    result = len(token) > 20
    logger.debug(f"🔍 بررسی توکن واتساپ: {token[:10]}... نتیجه: {result}")
    return result

# ==================== توابع امنیتی ====================

def validate_code_security(code):
    """بررسی امنیتی کد - جلوگیری از کدهای مخرب"""
    logger.debug("🔍 شروع بررسی امنیتی کد...")
    
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
            logger.warning(f"⚠️ کد مخرب شناسایی شد: {message}")
            return False, f"⚠️ {message}"
    
    logger.debug("✅ کد از نظر امنیتی معتبر است")
    return True, "✅ کد از نظر امنیتی معتبر است"

def inject_token_to_code(code, token, platform):
    """تزریق توکن به کد کاربر با روش‌های مختلف"""
    logger.debug(f"🔄 شروع تزریق توکن به کد (پلتفرم: {platform})...")
    
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
    
    logger.debug(f"✅ تزریق توکن انجام شد: {', '.join(modifications)}")
    return modified_code, f"✅ توکن با موفقیت تزریق شد ({', '.join(modifications)})"

# ==================== ایجاد فایل اجرایی ربات ====================

def create_bot_runner_file(code, token, platform):
    """ایجاد فایل کامل ربات با کد کاربر و تنظیمات"""
    logger.debug("🔄 ایجاد فایل اجرایی ربات...")
    
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
    
    logger.debug(f"✅ فایل اجرایی با {len(bot_code)} کاراکتر ایجاد شد")
    return bot_code

# ==================== اجرای کد ====================

def execute_python_code(code, token, platform):
    """اجرای کد پایتون در محیط ایزوله با مدیریت کامل"""
    logger.info("🔄 شروع اجرای کد...")
    
    is_valid, msg = validate_code_security(code)
    if not is_valid:
        logger.warning(f"❌ کد از نظر امنیتی رد شد: {msg}")
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
    
    logger.debug(f"📁 ذخیره فایل در: {temp_path}")
    
    try:
        with open(temp_path, 'w', encoding='utf-8') as f:
            f.write(full_code)
        logger.debug("✅ فایل ذخیره شد")
    except Exception as e:
        logger.error(f"❌ خطا در ایجاد فایل: {e}")
        logger.error(traceback.format_exc())
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
        logger.info(f"📦 نصب پکیج: {package_to_install}")
        try:
            install_result = subprocess.run(
                [sys.executable, "-m", "pip", "install", package_to_install, "--quiet"],
                capture_output=True,
                text=True,
                timeout=30
            )
            if install_result.returncode != 0:
                install_logs = f"⚠️ خطا در نصب {package_to_install}: {install_result.stderr[:200]}\n"
                logger.warning(install_logs)
            else:
                install_logs = f"✅ {package_to_install} نصب شد.\n"
                logger.info(install_logs)
        except Exception as e:
            install_logs = f"⚠️ خطا در نصب: {str(e)}\n"
            logger.error(install_logs)
    
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
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            cwd=temp_dir,
            bufsize=1,
            universal_newlines=True
        )
        
        logger.debug(f"📊 PID فرآیند: {process.pid}")
        
        try:
            stdout, stderr = process.communicate(timeout=EXECUTION_TIMEOUT)
            output = stdout + stderr
            success = process.returncode == 0
            
            logger.debug(f"📤 خروجی: {len(output)} کاراکتر")
            logger.debug(f"✅ کد خروجی: {process.returncode}")
            
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
            
    except FileNotFoundError as e:
        logger.error(f"❌ پایتون روی سرور نصب نیست: {e}")
        output = "❌ پایتون روی سرور نصب نیست!"
        success = False
        error_msg = "Python not found"
    except Exception as e:
        logger.error(f"❌ خطا در اجرا: {e}")
        logger.error(traceback.format_exc())
        output = f"❌ خطا در اجرا: {str(e)}"
        success = False
        error_msg = str(e)
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
    logger.info(f"📩 درخواست جدید: method={request.method}")
    
    if request.method == 'POST':
        try:
            # لاگ تمام داده‌های دریافتی
            logger.debug(f"📋 فرم داده‌ها: {dict(request.form)}")
            logger.debug(f"📎 فایل‌ها: {list(request.files.keys())}")
            
            # دریافت داده‌ها از فرم
            platform = request.form.get('platform', 'rubika')
            code_type = request.form.get('code_type', 'file')
            token = request.form.get('token', '').strip()
            
            logger.info(f"📩 دریافت درخواست: platform={platform}, code_type={code_type}, token={token[:10] if token else 'None'}...")
            
            # اعتبارسنجی توکن
            if not token:
                logger.warning("❌ توکن وارد نشده است")
                return render_template('result.html', 
                    result={'success': False, 'message': '❌ لطفاً توکن را وارد کنید', 'logs': ''})
            
            # ===== دریافت کد =====
            code_content = ""
            
            if code_type == 'file':
                logger.debug("📂 دریافت کد از فایل...")
                
                # ===== روش ۱: دریافت از فایل =====
                if 'code_file' in request.files:
                    file = request.files['code_file']
                    if file.filename != '':
                        logger.info(f"📄 دریافت فایل: {file.filename}")
                        file_content = file.read()
                        
                        if len(file_content) > MAX_CODE_SIZE:
                            logger.warning(f"❌ حجم فایل بیش از حد مجاز: {len(file_content)} بایت")
                            return render_template('result.html', 
                                result={'success': False, 'message': f'❌ حجم فایل بیشتر از {MAX_CODE_SIZE//1024} کیلوبایت است', 'logs': ''})
                        
                        try:
                            code_content = file_content.decode('utf-8', errors='ignore')
                            logger.info(f"✅ کد از فایل دریافت شد: {len(code_content)} کاراکتر")
                        except Exception as e:
                            logger.error(f"❌ خطا در خواندن فایل: {e}")
                            return render_template('result.html', 
                                result={'success': False, 'message': f'❌ خطا در خواندن فایل: {str(e)}', 'logs': ''})
                
                # ===== روش ۲: دریافت از code_text (اگر فایل ارسال نشده) =====
                if not code_content:
                    code_content = request.form.get('code_text', '').strip()
                    if code_content:
                        logger.info(f"✅ کد از متن دریافت شد (روش جایگزین): {len(code_content)} کاراکتر")
                    else:
                        logger.warning("❌ نه فایل و نه متن ارسال نشده است")
                        return render_template('result.html', 
                            result={'success': False, 'message': '❌ لطفاً یک فایل انتخاب کنید یا کد را وارد کنید', 'logs': ''})
                
            else:  # text
                logger.debug("✏️ دریافت کد از متن...")
                code_content = request.form.get('code_text', '').strip()
                if not code_content:
                    logger.warning("❌ کد وارد نشده است")
                    return render_template('result.html', 
                        result={'success': False, 'message': '❌ لطفاً کد را وارد کنید', 'logs': ''})
                logger.info(f"✅ کد از متن دریافت شد: {len(code_content)} کاراکتر")
            
            # بررسی اعتبار توکن بر اساس پلتفرم
            token_valid = False
            platform_name = ""
            
            if platform == 'rubika':
                platform_name = "روبیکا"
                token_valid = check_robika_token(token)
            elif platform == 'telegram':
                platform_name = "تلگرام"
                token_valid = check_telegram_token(token)
            elif platform == 'whatsapp':
                platform_name = "واتساپ"
                token_valid = check_whatsapp_token(token)
            else:
                platform_name = "سایر"
                token_valid = True
            
            logger.info(f"🔍 بررسی توکن {platform_name}: valid={token_valid}")
            
            if not token_valid and platform not in ['other', 'whatsapp']:
                logger.warning(f"❌ توکن {platform_name} نامعتبر است")
                return render_template('result.html', 
                    result={'success': False, 'message': f'❌ توکن {platform_name} نامعتبر است!', 
                           'logs': f'لطفاً توکن صحیح را از @BotFather در {platform_name} دریافت کنید'})
            
            # اجرای کد
            logger.info("🔄 شروع اجرای کد...")
            result = execute_python_code(code_content, token, platform)
            logger.info(f"✅ نتیجه اجرا: success={result['success']}")
            
            return render_template('result.html', result=result)
            
        except Exception as e:
            error_msg = traceback.format_exc()
            logger.error(f"❌ خطای سیستمی: {error_msg}")
            return render_template('result.html', 
                result={'success': False, 'message': f'❌ خطای سیستمی: {str(e)}', 
                       'logs': error_msg})
    
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
    logger.warning(f"❌ 404: {request.url}")
    return render_template('result.html', 
        result={'success': False, 'message': '❌ صفحه مورد نظر یافت نشد', 'logs': ''}), 404

@app.errorhandler(500)
def server_error(e):
    logger.error(f"❌ 500: {e}")
    return render_template('result.html', 
        result={'success': False, 'message': '❌ خطای داخلی سرور', 'logs': str(e)}), 500

@app.before_request
def log_request_info():
    """لاگ اطلاعات هر درخواست"""
    logger.debug(f"📨 {request.method} {request.url}")
    logger.debug(f"📋 Headers: {dict(request.headers)}")

@app.after_request
def log_response_info(response):
    """لاگ اطلاعات هر پاسخ"""
    logger.debug(f"📤 پاسخ: status={response.status_code}")
    return response

if __name__ == '__main__':
    print("\n" + "="*60)
    print("🤖 ربات رانر - راه‌انداز خودکار ربات‌ها (نسخه دیباگ)")
    print("="*60)
    print(f"📡 پورت: {PORT}")
    print(f"📄 حداکثر حجم فایل: {MAX_CODE_SIZE//1024} KB")
    print(f"⏱️ زمان اجرا: {EXECUTION_TIMEOUT} ثانیه")
    print(f"📱 پلتفرم‌های پشتیبانی‌شده: روبیکا، تلگرام، واتساپ")
    print("="*60)
    print("🌐 آدرس: http://localhost:" + str(PORT))
    print("📊 لاگ‌ها در Railway قابل مشاهده هستند")
    print("="*60 + "\n")
    
    app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True)
