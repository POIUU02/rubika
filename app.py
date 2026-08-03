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
# ===== کد ربات ادمین (برای خود شما) =====
# ============================================================
ADMIN_BOT_CODE = '''
import os
import sys
import time
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

TOKEN = "8262116870:AAGhf7siH7qpVm4nPAM8kGJcPwgyu0PZZFo"
ADMIN_ID = 6443963679

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if str(user.id) != str(ADMIN_ID):
        await update.message.reply_text("❌ شما دسترسی به این ربات ندارید!")
        return
    
    keyboard = [
        [InlineKeyboardButton("👤 مدیریت کاربران", callback_data="users")],
        [InlineKeyboardButton("💰 مدیریت مالی", callback_data="finance")],
        [InlineKeyboardButton("📩 رسیدها", callback_data="receipts")],
        [InlineKeyboardButton("⚙️ تنظیمات", callback_data="settings")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "⚙️ **پنل مدیریت**\n\n"
        "به پنل مدیریت خوش آمدید!",
        reply_markup=reply_markup
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    
    if str(user.id) != str(ADMIN_ID):
        await query.edit_message_text("❌ شما دسترسی ندارید!")
        return
    
    data = query.data
    if data == "users":
        await query.edit_message_text("👤 **مدیریت کاربران**\n\nدر حال توسعه...")
    elif data == "finance":
        await query.edit_message_text("💰 **مدیریت مالی**\n\nدر حال توسعه...")
    elif data == "receipts":
        await query.edit_message_text("📩 **رسیدها**\n\nدر حال توسعه...")
    elif data == "settings":
        await query.edit_message_text("⚙️ **تنظیمات**\n\nدر حال توسعه...")

def run_admin_bot():
    while True:
        try:
            logger.info("🚀 ربات ادمین شروع به کار کرد...")
            app = Application.builder().token(TOKEN).build()
            app.add_handler(CommandHandler("start", start))
            app.add_handler(CallbackQueryHandler(button_handler))
            app.run_polling()
        except Exception as e:
            logger.error(f"❌ خطا در ربات ادمین: {e}")
            time.sleep(5)

if __name__ == "__main__":
    run_admin_bot()
'''

# ============================================================
# ===== کد ربات کاربر (برای هر کاربر) =====
# ============================================================
def create_user_bot_code(user_code, user_token):
    """ساخت کد ربات برای کاربر با توکن خودش"""
    return f'''
import os
import sys
import time
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

TOKEN = "{user_token}"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🛒 خرید سرور", callback_data="buy")],
        [InlineKeyboardButton("📊 وضعیت", callback_data="status")],
        [InlineKeyboardButton("💰 کیف پول", callback_data="wallet")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "🤖 **ربات شما**\n\n"
        "سلام! به ربات خود خوش آمدید.",
        reply_markup=reply_markup
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "buy":
        await query.edit_message_text("🛒 **خرید سرور**\n\nبه زودی...")
    elif data == "status":
        await query.edit_message_text("📊 **وضعیت**\n\nدر حال توسعه...")
    elif data == "wallet":
        await query.edit_message_text("💰 **کیف پول**\n\nدر حال توسعه...")

def run_user_bot():
    while True:
        try:
            logger.info("🚀 ربات کاربر شروع به کار کرد...")
            app = Application.builder().token(TOKEN).build()
            app.add_handler(CommandHandler("start", start))
            app.add_handler(CallbackQueryHandler(button_handler))
            app.run_polling()
        except Exception as e:
            logger.error(f"❌ خطا در ربات کاربر: {{e}}")
            time.sleep(5)

if __name__ == "__main__":
    run_user_bot()
'''

# ============================================================
# ===== توابع اصلی سایت =====
# ============================================================

def check_telegram_token(token):
    """بررسی اعتبار توکن تلگرام"""
    try:
        url = f"https://api.telegram.org/bot{token}/getMe"
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200:
            return resp.json().get('ok', False)
        return False
    except:
        return False

def execute_bot(code, token):
    """اجرای ربات کاربر با توکن خودش"""
    logger.info(f"🔄 شروع اجرای ربات کاربر با توکن: {token[:10]}...")
    
    # ساخت کد کامل ربات با توکن کاربر
    full_code = create_user_bot_code(code, token)
    
    temp_dir = tempfile.mkdtemp()
    temp_path = os.path.join(temp_dir, "bot_runner.py")
    
    try:
        with open(temp_path, 'w', encoding='utf-8') as f:
            f.write(full_code)
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return {'success': False, 'message': f'❌ خطا: {str(e)}', 'logs': str(e)}
    
    # نصب پکیج
    install_logs = ""
    try:
        subprocess.run([sys.executable, "-m", "pip", "install", "python-telegram-bot", "--quiet"], capture_output=True, text=True, timeout=60)
        install_logs = "✅ python-telegram-bot نصب شد.\n"
    except:
        install_logs = "⚠️ خطا در نصب python-telegram-bot\n"
    
    # اجرای ربات (بدون محدودیت زمانی)
    output = ""
    success = False
    error_msg = ""
    process = None
    
    try:
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        
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
            
            # بررسی اعتبار توکن
            if not check_telegram_token(token):
                return render_template('result.html', 
                    result={'success': False, 'message': '❌ توکن تلگرام نامعتبر است!', 
                           'logs': 'لطفاً توکن صحیح را از @BotFather دریافت کنید'})
            
            # اجرای ربات کاربر
            result = execute_bot(code_content, token)
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
    print("🤖 ربات رانر - اجرای ربات کاربران")
    print("="*60)
    print(f"📡 پورت: {port}")
    print("🌐 آدرس: http://localhost:" + str(port))
    print("💡 هر کاربر با توکن خودش رباتش رو اجرا میکنه!")
    print("="*60 + "\n")
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
