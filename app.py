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

# ==================== تنظیمات لاگ ====================
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ==================== تنظیمات ====================
PORT = int(os.environ.get('PORT', 8080))
MAX_CODE_SIZE = 200 * 1024
EXECUTION_TIMEOUT = 120

# ==================== تابع نصب خودکار پکیج‌ها ====================
def auto_install_packages():
    required = ['flask', 'requests', 'psutil', 'gunicorn', 'python-telegram-bot']
    missing = []
    for pkg in required:
        try:
            importlib.import_module(pkg.replace('-', '_'))
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

# ==================== کد ربات تلگرام ====================
TELEGRAM_BOT_CODE = '''
# -*- coding: utf-8 -*-
import os
import sys
import asyncio
import logging
import json
import time
import sqlite3
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# ===== تنظیمات =====
TOKEN = "YOUR_TOKEN_HERE"
ADMIN_ID = 123456789

# ===== دیتابیس =====
DB_PATH = "bot_data.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT UNIQUE,
            username TEXT,
            balance INTEGER DEFAULT 0,
            is_admin BOOLEAN DEFAULT 0,
            created_at DATETIME
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS servers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            plan_name TEXT,
            storage INTEGER,
            duration INTEGER,
            price INTEGER,
            link_token TEXT UNIQUE,
            access_code TEXT,
            expires_at DATETIME,
            is_active BOOLEAN DEFAULT 1,
            created_at DATETIME
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS receipts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            amount INTEGER,
            receipt_image TEXT,
            status TEXT DEFAULT 'pending',
            created_at DATETIME
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            storage INTEGER,
            duration INTEGER,
            price INTEGER,
            is_active BOOLEAN DEFAULT 1
        )
    ''')
    
    plans = [
        ('عادی', 1, 1, 2000),
        ('ویژه', 2, 3, 5000),
        ('طلایی', 5, 7, 12000),
        ('الماس', 10, 30, 50000)
    ]
    for plan in plans:
        c.execute('SELECT * FROM plans WHERE name = ?', (plan[0],))
        if not c.fetchone():
            c.execute('INSERT INTO plans (name, storage, duration, price, is_active) VALUES (?, ?, ?, ?, ?)',
                      (plan[0], plan[1], plan[2], plan[3], 1))
    
    conn.commit()
    conn.close()

init_db()

def get_user(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT * FROM users WHERE user_id = ?', (str(user_id),))
    result = c.fetchone()
    conn.close()
    return result

def create_user(user_id, username):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('INSERT OR IGNORE INTO users (user_id, username, created_at) VALUES (?, ?, ?)',
              (str(user_id), username, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def get_plans():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT * FROM plans WHERE is_active = 1')
    result = c.fetchall()
    conn.close()
    return result

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    create_user(user.id, user.username)
    
    keyboard = [
        [InlineKeyboardButton("🛒 خرید سرور", callback_data="buy_server")],
        [InlineKeyboardButton("📊 وضعیت سرور من", callback_data="my_servers")],
        [InlineKeyboardButton("💰 کیف پول من", callback_data="wallet")],
        [InlineKeyboardButton("📝 اطلاعات کاربری", callback_data="profile")],
        [InlineKeyboardButton("📞 پشتیبانی", callback_data="support")],
    ]
    
    if str(user.id) == str(ADMIN_ID):
        keyboard.insert(0, [InlineKeyboardButton("⚙️ پنل مدیریت", callback_data="admin_panel")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🤖 **ربات مدیریت ربات‌ها**\n\n"
        "سلام! به ربات خوش آمدید.\n"
        "لطفاً یکی از گزینه‌های زیر را انتخاب کنید:",
        reply_markup=reply_markup
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = str(update.effective_user.id)
    data = query.data
    
    if data == "buy_server":
        plans = get_plans()
        text = "📋 **پلن‌های موجود:**\n\n"
        keyboard = []
        
        for plan in plans:
            text += f"🔹 {plan[1]} | {plan[2]}GB | {plan[3]} روز | {plan[4]:,} تومان\n"
            keyboard.append([InlineKeyboardButton(
                f"🔹 {plan[1]}", 
                callback_data=f"buy_{plan[0]}"
            )])
        
        keyboard.append([InlineKeyboardButton("⬅️ بازگشت", callback_data="back")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(text, reply_markup=reply_markup)
    
    elif data.startswith("buy_"):
        plan_id = int(data.split("_")[1])
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('SELECT * FROM plans WHERE id = ?', (plan_id,))
        plan = c.fetchone()
        conn.close()
        
        if plan:
            keyboard = [
                [InlineKeyboardButton("✅ تایید خرید", callback_data=f"confirm_{plan_id}")],
                [InlineKeyboardButton("❌ انصراف", callback_data="buy_server")],
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                f"📋 **جزئیات پلن {plan[1]}**\n\n"
                f"💾 حافظه: {plan[2]} GB\n"
                f"⏰ مدت: {plan[3]} روز\n"
                f"💰 قیمت: {plan[4]:,} تومان\n\n"
                f"آیا از خرید این پلن مطمئن هستید؟",
                reply_markup=reply_markup
            )
    
    elif data == "my_servers":
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('SELECT * FROM servers WHERE user_id = ? AND is_active = 1', (user_id,))
        servers = c.fetchall()
        conn.close()
        
        if servers:
            text = "📊 **وضعیت سرورهای شما:**\n\n"
            for server in servers:
                expires = datetime.fromisoformat(server[7])
                remaining = (expires - datetime.now()).days
                text += f"🔹 **سرور شماره {server[0]}**\n"
                text += f"├── پلن: {server[2]}\n"
                text += f"├── حافظه: {server[3]} GB\n"
                text += f"├── مدت: {server[4]} روز\n"
                text += f"├── باقی‌مانده: {remaining} روز\n"
                text += f"└── وضعیت: ✅ فعال\n\n"
            
            keyboard = [[InlineKeyboardButton("⬅️ بازگشت", callback_data="back")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(text, reply_markup=reply_markup)
        else:
            await query.edit_message_text(
                "❌ شما هیچ سرور فعالی ندارید.\n"
                "از بخش خرید سرور اقدام کنید.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🛒 خرید سرور", callback_data="buy_server")],
                    [InlineKeyboardButton("⬅️ بازگشت", callback_data="back")]
                ])
            )
    
    elif data == "wallet":
        user = get_user(user_id)
        balance = user[3] if user else 0
        
        keyboard = [
            [InlineKeyboardButton("💰 افزایش موجودی", callback_data="add_balance")],
            [InlineKeyboardButton("📜 تاریخچه تراکنش‌ها", callback_data="transactions")],
            [InlineKeyboardButton("⬅️ بازگشت", callback_data="back")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"💰 **کیف پول من**\n\n"
            f"موجودی فعلی: **{balance:,} تومان**",
            reply_markup=reply_markup
        )
    
    elif data == "profile":
        user = get_user(user_id)
        if user:
            await query.edit_message_text(
                f"📝 **اطلاعات کاربری**\n\n"
                f"👤 نام کاربری: @{user[2] or 'نامشخص'}\n"
                f"🆔 آیدی: {user[1]}\n"
                f"💰 موجودی: {user[3]:,} تومان\n"
                f"📅 تاریخ ثبت: {user[5][:10]}\n"
                f"👑 وضعیت: {'✅ ادمین' if user[4] else 'کاربر عادی'}",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ بازگشت", callback_data="back")]
                ])
            )
    
    elif data == "admin_panel":
        if str(update.effective_user.id) == str(ADMIN_ID):
            keyboard = [
                [InlineKeyboardButton("👤 مدیریت کاربران", callback_data="admin_users")],
                [InlineKeyboardButton("💰 مدیریت مالی", callback_data="admin_finance")],
                [InlineKeyboardButton("📩 رسیدها", callback_data="admin_receipts")],
                [InlineKeyboardButton("⚙️ تنظیمات", callback_data="admin_settings")],
                [InlineKeyboardButton("⬅️ بازگشت", callback_data="back")],
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(
                "⚙️ **پنل مدیریت**\n\n"
                "لطفاً یکی از گزینه‌های زیر را انتخاب کنید:",
                reply_markup=reply_markup
            )
    
    elif data == "back":
        await start(update, context)

def run_telegram_bot():
    try:
        app_bot = Application.builder().token(TOKEN).build()
        app_bot.add_handler(CommandHandler("start", start))
        app_bot.add_handler(CallbackQueryHandler(button_handler))
        app_bot.run_polling()
    except Exception as e:
        logger.error(f"❌ خطا در اجرای ربات تلگرام: {e}")
'''

# ==================== توابع بررسی توکن ====================

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

# ==================== تزریق توکن به کد ربات ====================

def inject_token_to_code(code, token):
    modified_code = code.replace("YOUR_TOKEN_HERE", token)
    return modified_code, f"✅ توکن با موفقیت در کد ربات تزریق شد"

# ==================== اجرای ربات ====================

def execute_bot(code, token):
    logger.info("🔄 شروع اجرای ربات...")
    
    code_with_token, msg = inject_token_to_code(code, token)
    
    temp_dir = tempfile.mkdtemp()
    temp_path = os.path.join(temp_dir, "bot_runner.py")
    
    try:
        with open(temp_path, 'w', encoding='utf-8') as f:
            f.write(code_with_token)
        logger.info(f"✅ فایل در {temp_path} ذخیره شد")
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return {'success': False, 'message': f'❌ خطا در ایجاد فایل: {str(e)}', 'logs': str(e)}
    
    install_logs = ""
    try:
        install_result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "python-telegram-bot", "--quiet"],
            capture_output=True, text=True, timeout=60
        )
        if install_result.returncode != 0:
            install_logs = f"⚠️ خطا در نصب python-telegram-bot\n"
        else:
            install_logs = f"✅ python-telegram-bot نصب شد.\n"
    except Exception as e:
        install_logs = f"⚠️ خطا در نصب: {str(e)}\n"
    
    output = ""
    success = False
    error_msg = ""
    process = None
    
    try:
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        
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
                
        except subprocess.TimeoutExpired:
            process.kill()
            output = f"⏰ زمان اجرا بیش از حد مجاز ({EXECUTION_TIMEOUT} ثانیه)\n"
            success = False
            error_msg = "Timeout"
            
    except Exception as e:
        output = f"❌ خطا در اجرا: {str(e)}"
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
        'message': '✅ ربات با موفقیت اجرا شد!' if success else f'❌ خطا در اجرا: {error_msg}',
        'logs': full_output,
        'error': error_msg if not success else ''
    }

# ==================== مسیرهای سایت ====================

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
            
            if not check_telegram_token(token):
                return render_template('result.html', 
                    result={'success': False, 'message': '❌ توکن تلگرام نامعتبر است!', 
                           'logs': 'لطفاً توکن صحیح را از @BotFather دریافت کنید'})
            
            result = execute_bot(code_content, token)
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
    port = int(os.environ.get('PORT', 8080))
    if port is None or port == 0:
        port = 8080
    print("\n" + "="*60)
    print("🤖 ربات رانر - راه‌انداز ربات تلگرام")
    print("="*60)
    print(f"📡 پورت: {port}")
    print(f"📄 حداکثر حجم فایل: {MAX_CODE_SIZE//1024} KB")
    print(f"⏱️ زمان اجرا: {EXECUTION_TIMEOUT} ثانیه")
    print(f"📱 پلتفرم پشتیبانی‌شده: تلگرام")
    print("="*60)
    print("🌐 آدرس: http://localhost:" + str(port))
    print("="*60 + "\n")
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
