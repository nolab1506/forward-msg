import os
import sqlite3
import time
import threading
import asyncio
import telebot
from telebot import types
from datetime import datetime, timedelta
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
    FloodWaitError
)

# ==================== CONFIG ====================
BOT_TOKEN = os.environ.get('BOT_TOKEN')
ADMIN_ID = int(os.environ.get('ADMIN_ID', '0'))
SUPPORT_USERNAME = os.environ.get('SUPPORT_USERNAME', 'nolab420')
API_ID = int(os.environ.get('API_ID', '36064494'))
API_HASH = os.environ.get('API_HASH', 'a72b7c3d63d2eb6b0f4b43f8d8cedbd0')
USDT_TRC20 = os.environ.get('USDT_TRC20', '')
USDT_BEP20 = os.environ.get('USDT_BEP20', '')

from PIL import Image, ImageDraw, ImageFont
import io
import tempfile
import os

bot = telebot.TeleBot(BOT_TOKEN)

# Active Telethon clients store
active_clients = {}
pending_auth = {}  # user_id -> {'client': ..., 'phone': ..., 'phone_code_hash': ...}
pending_target_selection = {}  # user_id -> [selected channel ids]  ✅ FIXED: separate from auth

PLANS = {
    'free': {
        'name': '🆓 Free Trial',
        'price': 0,
        'duration_days': 3,
        'max_targets': 5,
        'watermark': False,
        'text_replace': False,
        'description': '3 days, 5 channels, basic features'
    },
    'basic': {
        'name': '⭐ Basic',
        'price': 4,
        'duration_days': 30,
        'max_targets': 5,
        'watermark': False,
        'text_replace': False,
        'description': '$4/month, 5 channels'
    },
    'pro': {
        'name': '🚀 Pro',
        'price': 7,
        'duration_days': 30,
        'max_targets': 20,
        'watermark': True,
        'text_replace': True,
        'description': '$7/month, 20 channels, all features'
    },
    'ultra': {
        'name': '💎 Ultra',
        'price': 99,
        'duration_days': 30,
        'max_targets': 111,
        'watermark': True,
        'text_replace': True,
        'description': '$99/month, 111 channels, all features'
    }
}

# ==================== DATABASE ====================
def init_db():
    conn = sqlite3.connect('voidbridge.db')
    c = conn.cursor()

    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        plan TEXT DEFAULT NULL,
        started_at TEXT DEFAULT NULL,
        expires_at TEXT DEFAULT NULL,
        free_used INTEGER DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS sessions (
        user_id INTEGER PRIMARY KEY,
        phone TEXT,
        session_string TEXT,
        tg_name TEXT,
        tg_username TEXT,
        connected_at TEXT
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS filters (
        user_id INTEGER PRIMARY KEY,
        source_channel TEXT DEFAULT NULL,
        target_channels TEXT DEFAULT NULL,
        watermark TEXT DEFAULT '',
        silent_mode INTEGER DEFAULT 0,
        paused INTEGER DEFAULT 0,
        allow_text INTEGER DEFAULT 1,
        allow_photo INTEGER DEFAULT 1,
        allow_video INTEGER DEFAULT 1,
        allow_document INTEGER DEFAULT 1,
        allow_audio INTEGER DEFAULT 1,
        allow_sticker INTEGER DEFAULT 1,
        text_replace TEXT DEFAULT '',
        auto_delete_mins INTEGER DEFAULT 0
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS msg_map (
        source_msg_id INTEGER,
        user_id INTEGER,
        channel_id TEXT,
        sent_msg_id INTEGER,
        sent_at TEXT,
        PRIMARY KEY (source_msg_id, user_id, channel_id)
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS stats (
        user_id INTEGER PRIMARY KEY,
        total_forwarded INTEGER DEFAULT 0,
        total_edited INTEGER DEFAULT 0,
        total_deleted INTEGER DEFAULT 0
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS payments (
        payment_id TEXT PRIMARY KEY,
        user_id INTEGER,
        plan TEXT,
        amount REAL,
        txid TEXT,
        status TEXT DEFAULT 'pending',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')

    conn.commit()
    conn.close()

init_db()

# ==================== DB HELPERS ====================
def get_user(user_id):
    conn = sqlite3.connect('voidbridge.db')
    c = conn.cursor()
    c.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
    row = c.fetchone()
    conn.close()
    return row

def save_user(user_id, username):
    conn = sqlite3.connect('voidbridge.db')
    c = conn.cursor()
    c.execute('INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)', (user_id, username))
    conn.commit()
    conn.close()

def get_filter(user_id):
    conn = sqlite3.connect('voidbridge.db')
    c = conn.cursor()
    c.execute('SELECT * FROM filters WHERE user_id = ?', (user_id,))
    row = c.fetchone()
    conn.close()
    return row

ALLOWED_FILTER_FIELDS = {
    'source_channel', 'target_channels', 'watermark', 'silent_mode',
    'paused', 'allow_text', 'allow_photo', 'allow_video', 'allow_document',
    'allow_audio', 'allow_sticker', 'text_replace', 'auto_delete_mins'
}

def update_filter(user_id, **kwargs):
    conn = sqlite3.connect('voidbridge.db')
    c = conn.cursor()
    c.execute('INSERT OR IGNORE INTO filters (user_id) VALUES (?)', (user_id,))
    for key, val in kwargs.items():
        if key in ALLOWED_FILTER_FIELDS:
            c.execute(f'UPDATE filters SET {key} = ? WHERE user_id = ?', (val, user_id))
    conn.commit()
    conn.close()

ALLOWED_USER_FIELDS = {
    'username', 'plan', 'started_at', 'expires_at', 'free_used'
}

def update_user_field(user_id, **kwargs):
    conn = sqlite3.connect('voidbridge.db')
    c = conn.cursor()
    for key, val in kwargs.items():
        if key in ALLOWED_USER_FIELDS:
            c.execute(f'UPDATE users SET {key} = ? WHERE user_id = ?', (val, user_id))
    conn.commit()
    conn.close()

def is_subscribed(user_id):
    if user_id == ADMIN_ID:
        return True
    user = get_user(user_id)
    if not user or not user[4]:
        return False
    return datetime.fromisoformat(user[4]) > datetime.now()

def get_user_plan(user_id):
    user = get_user(user_id)
    if not user or not user[2]:
        return None
    return PLANS.get(user[2])

def update_stats(user_id, field):
    allowed = {'total_forwarded', 'total_edited', 'total_deleted'}
    if field not in allowed:
        return
    conn = sqlite3.connect('voidbridge.db')
    c = conn.cursor()
    c.execute('INSERT OR IGNORE INTO stats (user_id) VALUES (?)', (user_id,))
    c.execute(f'UPDATE stats SET {field} = {field} + 1 WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

def apply_replace(text, text_replace):
    if text_replace and '|' in text_replace:
        old, new = text_replace.split('|', 1)
        return text.replace(old, new)
    return text

def add_watermark_to_image(image_bytes, watermark_text):
    """Add watermark text to bottom-right of image. Returns bytes."""
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
        width, height = img.size

        # Create transparent overlay
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        # Font size relative to image size
        font_size = max(20, int(min(width, height) * 0.045))
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
        except:
            font = ImageFont.load_default()

        # Get text size
        bbox = draw.textbbox((0, 0), watermark_text, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]

        # Position: bottom-right with padding
        padding = int(font_size * 0.6)
        x = width - text_w - padding
        y = height - text_h - padding

        # Draw shadow/outline for readability
        for dx, dy in [(-2,-2),(2,-2),(-2,2),(2,2),(0,-2),(0,2),(-2,0),(2,0)]:
            draw.text((x+dx, y+dy), watermark_text, font=font, fill=(0, 0, 0, 180))

        # Draw white text
        draw.text((x, y), watermark_text, font=font, fill=(255, 255, 255, 230))

        # Merge overlay
        combined = Image.alpha_composite(img, overlay).convert("RGB")

        output = io.BytesIO()
        combined.save(output, format="JPEG", quality=92)
        output.seek(0)
        return output.read()
    except Exception as e:
        print(f"Watermark error: {e}")
        return image_bytes  # Return original if failed


def to_channel_id(raw_id):
    """Convert Telethon peer ID to proper Telegram channel ID (-100xxx format)."""
    cid = int(raw_id)
    if cid < 0:
        return cid  # already in correct format
    # Telethon returns bare positive IDs for channels/supergroups
    return int(f"-100{cid}")

def can_use_feature(user_id, feature):
    if user_id == ADMIN_ID:
        return True
    user = get_user(user_id)
    if not user or not user[2]:
        return False
    plan = PLANS.get(user[2], {})
    return plan.get(feature, False)

def get_session(user_id):
    conn = sqlite3.connect('voidbridge.db')
    c = conn.cursor()
    c.execute('SELECT * FROM sessions WHERE user_id = ?', (user_id,))
    row = c.fetchone()
    conn.close()
    return row

def save_session(user_id, phone, session_string, tg_name, tg_username):
    conn = sqlite3.connect('voidbridge.db')
    c = conn.cursor()
    c.execute('''INSERT OR REPLACE INTO sessions 
        (user_id, phone, session_string, tg_name, tg_username, connected_at)
        VALUES (?, ?, ?, ?, ?, ?)''',
        (user_id, phone, session_string, tg_name, tg_username, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()

def delete_session(user_id):
    conn = sqlite3.connect('voidbridge.db')
    c = conn.cursor()
    c.execute('DELETE FROM sessions WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

def get_expiry_countdown(user_id):
    user = get_user(user_id)
    if not user or not user[4]:
        return "No Plan"
    exp = datetime.fromisoformat(user[4])
    diff = exp - datetime.now()
    if diff.total_seconds() <= 0:
        return "⚠️ Expired"
    days = diff.days
    hours = diff.seconds // 3600
    mins = (diff.seconds % 3600) // 60
    secs = diff.seconds % 60
    return f"{days}d {hours}h {mins}m {secs}s"

# ==================== ASYNCIO LOOP ====================
loop = asyncio.new_event_loop()

def run_async(coro):
    return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout=30)

def start_loop():
    asyncio.set_event_loop(loop)
    loop.run_forever()

threading.Thread(target=start_loop, daemon=True).start()

# ==================== TELETHON HELPERS ====================
async def create_client(session_string=None):
    client = TelegramClient(
        StringSession(session_string or ''),
        API_ID, API_HASH
    )
    await client.connect()
    return client

async def send_otp(user_id, phone):
    client = await create_client()
    result = await client.send_code_request(phone)
    pending_auth[user_id] = {
        'client': client,
        'phone': phone,
        'phone_code_hash': result.phone_code_hash
    }
    return True

async def verify_otp(user_id, code):
    auth = pending_auth.get(user_id)
    if not auth:
        return None, "Session expired. /start again."
    try:
        await auth['client'].sign_in(
            auth['phone'],
            code,
            phone_code_hash=auth['phone_code_hash']
        )
        me = await auth['client'].get_me()
        session_string = auth['client'].session.save()
        save_session(
            user_id,
            auth['phone'],
            session_string,
            f"{me.first_name or ''} {me.last_name or ''}".strip(),
            me.username or ''
        )
        active_clients[user_id] = auth['client']
        del pending_auth[user_id]
        return me, None
    except PhoneCodeInvalidError:
        return None, "❌ Invalid code!"
    except PhoneCodeExpiredError:
        return None, "❌ Code expired! Try again."
    except SessionPasswordNeededError:
        return None, "2FA_NEEDED"

async def verify_2fa(user_id, password):
    auth = pending_auth.get(user_id)
    if not auth:
        return None, "Session expired."
    try:
        await auth['client'].sign_in(password=password)
        me = await auth['client'].get_me()
        session_string = auth['client'].session.save()
        save_session(
            user_id,
            auth['phone'],
            session_string,
            f"{me.first_name or ''} {me.last_name or ''}".strip(),
            me.username or ''
        )
        active_clients[user_id] = auth['client']
        del pending_auth[user_id]
        return me, None
    except Exception as e:
        return None, f"❌ Wrong password: {e}"

async def get_user_channels(user_id):
    # LOOP FIX: always create a fresh client on the global loop
    # Never reuse active_clients[user_id] which lives on the listener's own loop
    sess = get_session(user_id)
    if not sess:
        return []
    client = TelegramClient(StringSession(sess[2]), API_ID, API_HASH)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            return []
        dialogs = await client.get_dialogs()
        channels = []
        for d in dialogs:
            if d.is_channel or d.is_group:
                channels.append({
                    'id': d.id,
                    'name': d.name,
                    'type': 'Channel' if d.is_channel else 'Group'
                })
        return channels
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass

async def load_client(user_id):
    if user_id in active_clients:
        return active_clients[user_id]
    sess = get_session(user_id)
    if not sess:
        return None
    try:
        client = await create_client(sess[2])
        if not await client.is_user_authorized():
            return None
        active_clients[user_id] = client
        return client
    except:
        return None

# ==================== START ====================
@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.from_user.id
    username = message.from_user.username or 'Unknown'
    save_user(user_id, username)
    update_filter(user_id)

    sess = get_session(user_id)
    if sess:
        show_main_menu(user_id)
    else:
        show_connect_screen(user_id)

def show_connect_screen(chat_id):
    markup = types.InlineKeyboardMarkup()
    markup.row(types.InlineKeyboardButton('🔗 Connect Telegram Account', callback_data='connect_account'))
    markup.row(types.InlineKeyboardButton('💰 View Plans', callback_data='show_plans'))

    text = (
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🌌 *VOIDBRIDGE PLATFORM*\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🔗 *Enterprise Message Routing & Automation*\n\n"
        "⚙️ *System Configurations Available:*\n\n"
        "◼️ *Forwarding:* Fully Automated & Real-time\n"
        "◼️ *Sync Engine:* Live Edit & Mutation Tracker\n"
        "◼️ *Privacy:* Stealth & Silent Operations\n"
        "◼️ *Parsing:* High-speed Text & Link Replacement\n"
        "◼️ *Scheduler:* Auto Delete & Purge System\n"
        "◼️ *Filter Logic:* Premium Media & Caption Filter\n"
        "◼️ *Switching:* Instant Pause/Resume Controller\n\n"
        "🔐 *Connect your Telegram account to get started* 👇"
    )
    bot.send_message(chat_id, text, reply_markup=markup, parse_mode='Markdown')

def show_main_menu(chat_id, msg_id=None):
    sess = get_session(chat_id)

    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton('⚙️ Source Channel', callback_data='set_source'),
        types.InlineKeyboardButton('📡 Target Channels', callback_data='set_targets')
    )
    markup.row(
        types.InlineKeyboardButton('🔖 Watermark', callback_data='set_watermark'),
        types.InlineKeyboardButton('🔁 Text Replace', callback_data='set_replace')
    )
    markup.row(
        types.InlineKeyboardButton('🎚️ Filters', callback_data='filter_menu'),
        types.InlineKeyboardButton('🔧 Settings', callback_data='settings_menu')
    )
    markup.row(
        types.InlineKeyboardButton('📊 Stats', callback_data='show_stats'),
        types.InlineKeyboardButton('👤 My Account', callback_data='my_account')
    )
    markup.row(
        types.InlineKeyboardButton('📋 My Channels', callback_data='my_channels'),
        types.InlineKeyboardButton('📘 Guide', callback_data='guide_menu')
    )
    markup.row(
        types.InlineKeyboardButton('💳 Plans', callback_data='show_plans'),
        types.InlineKeyboardButton('🆘 Support', url=f'https://t.me/{SUPPORT_USERNAME}')
    )
    markup.row(
        types.InlineKeyboardButton('⚡ Disconnect Account', callback_data='disconnect_account')
    )

    user = get_user(chat_id)
    plan_key = user[2] if user else None
    plan_name = PLANS[plan_key]['name'] if plan_key and plan_key in PLANS else 'No Plan'
    expiry_str = get_expiry_countdown(chat_id)

    f = get_filter(chat_id)
    target_count = len(f[2].split(',')) if f and f[2] else 0

    tg_name = sess[3] if sess else 'N/A'
    tg_username = f"@{sess[4]}" if sess and sess[4] else ''

    conn2 = sqlite3.connect('voidbridge.db')
    c2 = conn2.cursor()
    c2.execute('SELECT total_forwarded FROM stats WHERE user_id = ?', (chat_id,))
    stats_row = c2.fetchone()
    conn2.close()
    total_fwd = stats_row[0] if stats_row else 0

    source_ch = f[1] if f and f[1] else 'Not Set'
    target_ch_raw = f[2] if f and f[2] else ''
    target_list = [t.strip() for t in target_ch_raw.split(',') if t.strip()]
    target_count_num = len(target_list)
    outbound_display = target_list[0] if target_list else 'Not Set'
    paused = f[4] if f else 0
    pipeline_status = '[ RUNNING ]' if not paused else '[ PAUSED  ]'

    text = (
        f"VB / MANAGEMENT CONSOLE\n\n"
        f"Uptime Verification │  99.9% Stable\n"
        f"Encryption Protocol │  TLS 1.3\n\n"
        f"VoidBridge v6.2 updated\n"
        f"Active status : online 🟢\n\n"
        f"Select an action from the secure panel below."
    )

    try:
        if msg_id:
            bot.edit_message_text(text, chat_id, msg_id, reply_markup=markup)
        else:
            bot.send_message(chat_id, text, reply_markup=markup)
    except:
        bot.send_message(chat_id, text, reply_markup=markup)

# ==================== CONNECT ACCOUNT ====================
@bot.callback_query_handler(func=lambda c: c.data == 'connect_account')
def connect_account(call):
    bot.answer_callback_query(call.id)
    markup = types.InlineKeyboardMarkup()
    markup.row(types.InlineKeyboardButton('🚀 Connect Now', callback_data='start_connect'))
    markup.row(types.InlineKeyboardButton('⬅️ Back', callback_data='back_to_welcome'))

    bot.send_message(
        call.message.chat.id,
        "📱 *TELEGRAM ACCOUNT CONNECT*\n\n"
        "Please enter your phone number with country code:\n\n"
        "▸ Format:   `+[code][number]`\n"
        "▸ Example:  `+1XXXXXXXXXX`\n\n"
        "📌 *Notes:*\n"
        "▸ Use your own country code\n"
        "▸ No spaces or special characters\n"
        "▸ After entering, you'll receive an OTP\n"
        "▸ 2FA password will be asked if enabled",
        parse_mode='Markdown',
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda c: c.data == 'start_connect')
def start_connect(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(
        call.message.chat.id,
        "📲 Enter your phone number:",
        parse_mode='Markdown'
    )
    bot.register_next_step_handler(msg, process_phone)

@bot.callback_query_handler(func=lambda c: c.data == 'back_to_welcome')
def back_to_welcome(call):
    bot.answer_callback_query(call.id)
    show_connect_screen(call.message.chat.id)

def process_phone(message):
    user_id = message.from_user.id
    phone = message.text.strip()

    if not phone.startswith('+'):
        phone = '+' + phone

    bot.send_message(user_id, "⏳ Sending OTP...")

    try:
        run_async(send_otp(user_id, phone))
        msg = bot.send_message(
            user_id,
            "✅ OTP Sent!\n\n"
            "📨 Enter the code sent to your Telegram:\n\n"
            "⚠️ Format: `12345` (numbers only)\n"
            "or with prefix: `mycode12345`",
            parse_mode='Markdown'
        )
        bot.register_next_step_handler(msg, process_otp)
    except FloodWaitError as e:
        bot.send_message(user_id, f"❌ Flood wait! Try again in {e.seconds} seconds.")
    except Exception as e:
        bot.send_message(user_id, f"❌ Error: {str(e)}\n\nPlease try again with /start")

def process_otp(message):
    user_id = message.from_user.id
    code = message.text.strip().replace('mycode', '').replace(' ', '')

    bot.send_message(user_id, "⏳ Verifying...")

    try:
        me, error = run_async(verify_otp(user_id, code))
        if error == "2FA_NEEDED":
            msg = bot.send_message(
                user_id,
                "🔐 *Two-Factor Authentication*\n\n"
                "Enter your 2FA password:",
                parse_mode='Markdown'
            )
            bot.register_next_step_handler(msg, process_2fa)
            return
        if error:
            bot.send_message(user_id, error)
            return
        if me:
            name = f"{me.first_name or ''} {me.last_name or ''}".strip()
            bot.send_message(
                user_id,
                f"✅ *Successfully Connected!*\n\n"
                f"*Account Information:*\n"
                f"• 👤 Name: *{name}*\n"
                f"• 🆔 User ID: `{me.id}`\n"
                f"• 📎 Username: @{me.username or 'N/A'}\n"
                f"• 📱 Phone: `{me.phone}`\n\n"
                f"Welcome to VoidBridge Auto Forward Bot! 🎉\n"
                f"Use the buttons below to get started 👇",
                parse_mode='Markdown'
            )
            # Start forwarding listener for this user
            threading.Thread(
                target=start_user_listener,
                args=(user_id,),
                daemon=True
            ).start()
            show_main_menu(user_id)
    except Exception as e:
        bot.send_message(user_id, f"❌ Error: {str(e)}")

def process_2fa(message):
    user_id = message.from_user.id
    password = message.text.strip()

    bot.send_message(user_id, "⏳ Verifying 2FA...")

    try:
        me, error = run_async(verify_2fa(user_id, password))
        if error:
            bot.send_message(user_id, error)
            return
        if me:
            name = f"{me.first_name or ''} {me.last_name or ''}".strip()
            sess = get_session(user_id)
            phone = sess[1] if sess else 'N/A'
            bot.send_message(
                user_id,
                f"✅ *Successfully Connected!*\n\n"
                f"*Account Information:*\n"
                f"• 👤 Name: *{name}*\n"
                f"• 🆔 User ID: `{me.id}`\n"
                f"• 📎 Username: @{me.username or 'N/A'}\n"
                f"• 📱 Phone: `{phone}`\n\n"
                f"Welcome to VoidBridge Auto Forward Bot! 🎉\n"
                f"Use the buttons below to get started 👇",
                parse_mode='Markdown'
            )
            threading.Thread(
                target=start_user_listener,
                args=(user_id,),
                daemon=True
            ).start()
            show_main_menu(user_id)
    except Exception as e:
        bot.send_message(user_id, f"❌ Error: {str(e)}")

@bot.callback_query_handler(func=lambda c: c.data == 'disconnect_account')
def disconnect_account(call):
    user_id = call.message.chat.id
    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton('✅ Yes, Disconnect', callback_data='confirm_disconnect'),
        types.InlineKeyboardButton('❌ No', callback_data='go_home')
    )
    bot.edit_message_text(
        "⚠️ *Disconnect Account?*\n\n"
        "This will stop all forwarding!",
        call.message.chat.id, call.message.message_id,
        reply_markup=markup, parse_mode='Markdown'
    )

@bot.callback_query_handler(func=lambda c: c.data == 'confirm_disconnect')
def confirm_disconnect(call):
    user_id = call.message.chat.id
    if user_id in active_clients:
        try:
            run_async(active_clients[user_id].disconnect())
        except:
            pass
        del active_clients[user_id]
    delete_session(user_id)
    bot.edit_message_text(
        "✅ Account disconnected!\n\nUse /start to connect again.",
        call.message.chat.id, call.message.message_id
    )

# ==================== MY CHANNELS ====================
@bot.callback_query_handler(func=lambda c: c.data == 'my_channels')
def my_channels(call):
    user_id = call.message.chat.id
    sess = get_session(user_id)
    if not sess:
        bot.answer_callback_query(call.id, "❌ Please connect your account first!")
        return

    bot.answer_callback_query(call.id)
    bot.send_message(user_id, "⏳ Loading your channels...")

    try:
        channels = run_async(get_user_channels(user_id))
        if not channels:
            bot.send_message(user_id, "❌ No channels or groups found!")
            return

        text = "📋 *Your Channels & Groups:*\n\n"
        for ch in channels[:50]:
            ctype = "📢" if ch['type'] == 'Channel' else "👥"
            ch_id = to_channel_id(ch['id'])  # ✅ FIXED
            text += f"{ctype} *{ch['name']}*\n└ ID: `{ch_id}`\n\n"

        markup = types.InlineKeyboardMarkup()
        markup.row(types.InlineKeyboardButton('⬅️ Back', callback_data='go_home'))

        bot.send_message(user_id, text, reply_markup=markup, parse_mode='Markdown')
    except Exception as e:
        bot.send_message(user_id, f"❌ Error: {str(e)}")

# ==================== GUIDE MENU ====================
@bot.callback_query_handler(func=lambda c: c.data == 'guide_menu')
def guide_menu(call):
    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton('📥 Source Setup', callback_data='guide_source'),
        types.InlineKeyboardButton('📤 Target Setup', callback_data='guide_target')
    )
    markup.row(
        types.InlineKeyboardButton('🎛️ Filters', callback_data='guide_filters'),
        types.InlineKeyboardButton('⚙️ Settings', callback_data='guide_settings')
    )
    markup.row(
        types.InlineKeyboardButton('💰 Plans', callback_data='guide_plans'),
        types.InlineKeyboardButton('📊 Stats', callback_data='guide_stats')
    )
    markup.row(
        types.InlineKeyboardButton('🔤 Text Replace', callback_data='guide_replace'),
        types.InlineKeyboardButton('💧 Watermark', callback_data='guide_watermark')
    )
    markup.row(types.InlineKeyboardButton(f'🆘 Support', url=f'https://t.me/{SUPPORT_USERNAME}'))
    markup.row(types.InlineKeyboardButton('⬅️ Back', callback_data='go_home'))

    bot.edit_message_text(
        "📖 *VoidBridge Guide*\n\n"
        "What would you like to know?",
        call.message.chat.id, call.message.message_id,
        reply_markup=markup, parse_mode='Markdown'
    )

@bot.callback_query_handler(func=lambda c: c.data.startswith('guide_'))
def show_guide(call):
    guides = {
        'guide_source': (
            "📥 *Source Channel Setup*\n\n"
            "Source is the channel messages will be forwarded from.\n\n"
            "*How to set up:*\n\n"
            "1️⃣ Click *📥 Source Channel* on the dashboard\n"
            "2️⃣ Enter the Channel ID\n\n"
            "*How to find Channel ID:*\n"
            "• Click *📋 My Channels* on the dashboard\n"
            "• All channel IDs will be listed there\n\n"
            "*Example ID:* `-1001234567890`\n\n"
            "⚠️ IDs always start with `-100`"
        ),
        'guide_target': (
            "📤 *Target Channels Setup*\n\n"
            "Target channels are where messages will be forwarded to.\n\n"
            "*How to set up:*\n\n"
            "1️⃣ Click *📤 Target Channels* on the dashboard\n"
            "2️⃣ Enter channel IDs separated by commas\n\n"
            "*Example:*\n"
            "`-1001111,-1002222,-1003333`\n\n"
            "⚠️ Max channel limit depends on your plan\n\n"
            "💡 Copy IDs from *My Channels*"
        ),
        'guide_filters': (
            "🎛️ *Media Filters*\n\n"
            "Control which content types get forwarded.\n\n"
            "*Available Filters:*\n\n"
            "📝 *Text* — Text message\n"
            "🖼️ *Photo* — Images\n"
            "🎥 *Video* — Videos\n"
            "📄 *Document* — Files\n"
            "🎵 *Audio* — Audio files\n"
            "🎭 *Sticker* — Stickers\n\n"
            "*How to:*\n"
            "Dashboard → 🎛️ Filters → Toggle on/off"
        ),
        'guide_settings': (
            "⚙️ *Settings Guide*\n\n"
            "🔇 *Silent Mode*\n"
            "└ Forwards messages without notification\n\n"
            "⏸️ *Pause/Resume*\n"
            "└ Temporarily pause or resume forwarding\n\n"
            "🗑️ *Auto Delete*\n"
            "└ Auto-delete forwarded messages after X minutes\n"
            "└ 0 = disabled"
        ),
        'guide_plans': (
            "💰 *Plans Guide*\n\n"
            "🆓 *Free Trial* — 3 days\n"
            "└ 5 channels, all features\n\n"
            "⭐ *Basic — $4/month*\n"
            "└ 5 channels\n\n"
            "🚀 *Pro — $7/month*\n"
            "└ 20 channels, Watermark, Text Replace\n\n"
            "💎 *Ultra — $99/month*\n"
            "└ 111 channels, all features\n\n"
            "*Payment:* USDT TRC20\n"
            "*Support:* @" + SUPPORT_USERNAME
        ),
        'guide_stats': (
            "📊 *Stats Guide*\n\n"
            "View all your forwarding statistics here.\n\n"
            "📨 *Total Forwarded* — Total messages forwarded\n"
            "✏️ *Total Edited* — Messages synced via edit\n"
            "🗑️ *Total Deleted* — Messages auto-deleted\n\n"
            "Dashboard → 📊 Stats"
        ),
        'guide_replace': (
            "🔤 *Text Replace Guide*\n\n"
            "Automatically replace text in forwarded messages.\n\n"
            "*Format:* `old text|new text`\n\n"
            "*Example:*\n"
            "`@oldchannel|@newchannel`\n\n"
            "This will replace all `@oldchannel` → `@newchannel` in messages\n\n"
            "⚠️ This feature is available on *Pro* and *Ultra* plans"
        ),
        'guide_watermark': (
            "💧 *Watermark Guide*\n\n"
            "Your watermark will be added to every forwarded message.\n\n"
            "*How to set up:*\n\n"
            "1️⃣ Dashboard → 💧 Watermark\n"
            "2️⃣ Enter your watermark text\n\n"
            "*Example:*\n"
            "`© My Channel @username`\n\n"
            "Send `off` to disable\n\n"
            "⚠️ This feature is available on *Pro* and *Ultra* plans"
        ),
    }

    key = call.data
    text = guides.get(key, "❌ Guide not found!")

    markup = types.InlineKeyboardMarkup()
    markup.row(types.InlineKeyboardButton(f'🆘 Support', url=f'https://t.me/{SUPPORT_USERNAME}'))
    markup.row(types.InlineKeyboardButton('⬅️ Back to Guide', callback_data='guide_menu'))

    try:
        bot.edit_message_text(
            text, call.message.chat.id, call.message.message_id,
            reply_markup=markup, parse_mode='Markdown'
        )
    except:
        pass

# ==================== PLANS ====================
@bot.callback_query_handler(func=lambda c: c.data == 'show_plans')
def show_plans(call):
    markup = types.InlineKeyboardMarkup()
    markup.row(types.InlineKeyboardButton('🆓 Free Trial (3 days)', callback_data='buy_free'))
    markup.row(types.InlineKeyboardButton('⭐ Basic — $4/month (5 ch)', callback_data='buy_basic'))
    markup.row(types.InlineKeyboardButton('🚀 Pro — $7/month (20 ch)', callback_data='buy_pro'))
    markup.row(types.InlineKeyboardButton('💎 Ultra — $99/month (111 ch)', callback_data='buy_ultra'))
    markup.row(types.InlineKeyboardButton('⬅️ Back', callback_data='go_home'))

    try:
        bot.edit_message_text(
            "💰 *Available Plans*\n\n"
            "🆓 *Free Trial*\n"
            "└ 3 days | 5 channels | all features\n\n"
            "⭐ *Basic — $4/month*\n"
            "└ 5 channels | Watermark ❌ | Text Replace ❌\n\n"
            "🚀 *Pro — $7/month*\n"
            "└ 20 channels | Watermark ✅ | all features\n\n"
            "💎 *Ultra — $99/month*\n"
            "└ 111 channels | all features\n\n"
            "Select a plan below 👇",
            call.message.chat.id, call.message.message_id,
            reply_markup=markup, parse_mode='Markdown'
        )
    except Exception:
        bot.send_message(
            call.message.chat.id,
            "💰 *Available Plans*\n\n"
            "🆓 *Free Trial*\n"
            "└ 3 days | 5 channels | all features\n\n"
            "⭐ *Basic — $4/month*\n"
            "└ 5 channels | Watermark ❌ | Text Replace ❌\n\n"
            "🚀 *Pro — $7/month*\n"
            "└ 20 channels | Watermark ✅ | all features\n\n"
            "💎 *Ultra — $99/month*\n"
            "└ 111 channels | all features\n\n"
            "Select a plan below 👇",
            reply_markup=markup, parse_mode='Markdown'
        )

@bot.callback_query_handler(func=lambda c: c.data.startswith('buy_'))
def buy_plan(call):
    user_id = call.message.chat.id
    plan_key = call.data.split('_')[1]
    plan = PLANS[plan_key]

    if plan_key == 'free':
        user = get_user(user_id)
        if user and user[5] == 1:  # BUG FIX: explicit check ✅
            bot.answer_callback_query(call.id, "❌ You have already used the Free Trial!")
            return
        expires = (datetime.now() + timedelta(days=3)).isoformat()
        update_user_field(
            user_id,
            plan='free',
            started_at=datetime.now().isoformat(),
            expires_at=expires,
            free_used=1
        )
        bot.edit_message_text(
            "✅ *Free Trial Activated!*\n\n"
            "Plan: *Free Trial*\n"
            f"Expires: *{expires[:10]}*\n\n"
            "Enjoy all features for 3 days! 🚀",
            call.message.chat.id, call.message.message_id,
            parse_mode='Markdown'
        )
        return

    markup = types.InlineKeyboardMarkup()
    markup.row(types.InlineKeyboardButton('✅ I have paid', callback_data=f'paid_{plan_key}'))
    markup.row(types.InlineKeyboardButton('⬅️ Back', callback_data='show_plans'))

    bot.edit_message_text(
        f"💳 *Payment Details*\n\n"
        f"▸ Plan:    *{plan['name']}*\n"
        f"▸ Amount:  *${plan['price']} USDT*\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 *Wallet Addresses:*\n\n"
        f"🔹 *TRC20 (TRON):*\n"
        f"`{USDT_TRC20}`\n\n"
        f"🔸 *BEP20 (BSC):*\n"
        f"`{USDT_BEP20}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"⚠️ *Notes:*\n"
        f"▸ Send exact amount\n"
        f"▸ Use correct network\n"
        f"▸ Wrong network = lost funds\n\n"
        f"After payment, press the button below 👇",
        call.message.chat.id, call.message.message_id,
        reply_markup=markup, parse_mode='Markdown'
    )

pending_payment = set()  # track users already in payment flow

@bot.callback_query_handler(func=lambda c: c.data.startswith('paid_'))
def paid_plan(call):
    user_id = call.message.chat.id
    plan_key = call.data.split('_')[1]
    plan = PLANS[plan_key]

    # Prevent duplicate flow
    if user_id in pending_payment:
        bot.answer_callback_query(call.id, "⏳ Already waiting for your TxID!")
        return
    pending_payment.add(user_id)

    # Remove the button so it can't be clicked again
    try:
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
    except:
        pass

    markup = types.InlineKeyboardMarkup()
    markup.row(types.InlineKeyboardButton('❌ Cancel', callback_data='cancel_payment'))

    msg = bot.send_message(
        user_id,
        "📋 Send your *Transaction ID (TxID)*:\n\n"
        "Press Cancel to go back.",
        parse_mode='Markdown',
        reply_markup=markup
    )

    def get_txid(message):
        pending_payment.discard(user_id)
        if message.text.strip().lower() == 'cancel':
            bot.send_message(user_id, "❌ Payment cancelled.")
            return
        txid = message.text.strip()
        conn = sqlite3.connect('voidbridge.db')
        c = conn.cursor()
        pid = f"{user_id}_{plan_key}_{int(time.time())}"
        c.execute(
            'INSERT INTO payments VALUES (?, ?, ?, ?, ?, ?, ?)',
            (pid, user_id, plan_key, plan['price'], txid, 'pending', datetime.now().isoformat())
        )
        conn.commit()
        conn.close()

        markup = types.InlineKeyboardMarkup()
        markup.row(
            types.InlineKeyboardButton('✅ Approve', callback_data=f'approve_{user_id}_{plan_key}'),
            types.InlineKeyboardButton('❌ Reject', callback_data=f'reject_{user_id}')
        )
        bot.send_message(
            ADMIN_ID,
            f"💰 *New Payment!*\n\n"
            f"User: `{user_id}`\n"
            f"@{message.from_user.username or 'N/A'}\n"
            f"Plan: *{plan['name']}*\n"
            f"Amount: *${plan['price']}*\n"
            f"TxID: `{txid}`",
            reply_markup=markup, parse_mode='Markdown'
        )
        bot.send_message(user_id, "⏳ Verifying your payment, please wait...")

    bot.register_next_step_handler(msg, get_txid)

@bot.callback_query_handler(func=lambda c: c.data == 'cancel_payment')
def cancel_payment(call):
    bot.answer_callback_query(call.id)
    pending_payment.discard(call.message.chat.id)
    bot.edit_message_text(
        "❌ Payment cancelled.",
        call.message.chat.id,
        call.message.message_id
    )
    show_main_menu(call.message.chat.id)

@bot.callback_query_handler(func=lambda c: c.data.startswith('approve_'))
def approve_payment(call):
    if call.from_user.id != ADMIN_ID:
        return
    parts = call.data.split('_')
    target_user = int(parts[1])
    plan_key = parts[2]
    plan = PLANS[plan_key]
    now = datetime.now()
    expires = (now + timedelta(days=plan['duration_days'])).isoformat()
    update_user_field(target_user, plan=plan_key, started_at=now.isoformat(), expires_at=expires)

    # BUG FIX: update payment status in DB ✅
    conn = sqlite3.connect('voidbridge.db')
    c = conn.cursor()
    c.execute(
        "UPDATE payments SET status = 'approved' WHERE user_id = ? AND plan = ? AND status = 'pending'",
        (target_user, plan_key)
    )
    conn.commit()
    conn.close()

    bot.send_message(
        target_user,
        f"✅ *Payment Approved!*\n\n"
        f"Plan: *{plan['name']}*\n"
        f"Expires: *{expires[:10]}*\n\n"
        f"Use /start to go to your dashboard! 🚀",
        parse_mode='Markdown'
    )
    bot.answer_callback_query(call.id, "✅ Approved!")
    bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id)

@bot.callback_query_handler(func=lambda c: c.data.startswith('reject_'))
def reject_payment(call):
    if call.from_user.id != ADMIN_ID:
        return
    target_user = int(call.data.split('_')[1])
    bot.send_message(
        target_user,
        f"❌ Payment rejected.\n"
        f"Contact @{SUPPORT_USERNAME} for support."
    )
    bot.answer_callback_query(call.id, "❌ Rejected!")

# ==================== MY ACCOUNT ====================
@bot.callback_query_handler(func=lambda c: c.data == 'my_account')
def my_account(call):
    user_id = call.message.chat.id
    user = get_user(user_id)
    f = get_filter(user_id)
    sess = get_session(user_id)

    plan_key = user[2] if user and user[2] else None
    plan_name = PLANS[plan_key]['name'] if plan_key else 'No Plan'
    started = user[3][:10] if user and user[3] else 'N/A'
    expires = user[4][:10] if user and user[4] else 'N/A'
    status = "✅ Active" if is_subscribed(user_id) else "❌ Expired"
    source = f[1] if f and f[1] else 'Not set'
    targets = len(f[2].split(',')) if f and f[2] else 0
    countdown = get_expiry_countdown(user_id)

    tg_name = sess[3] if sess else 'Not connected'
    tg_user = f"@{sess[4]}" if sess and sess[4] else 'N/A'

    markup = types.InlineKeyboardMarkup()
    markup.row(types.InlineKeyboardButton('💰 Upgrade Plan', callback_data='show_plans'))
    markup.row(types.InlineKeyboardButton('🔄 Refresh Countdown', callback_data='my_account'))
    markup.row(types.InlineKeyboardButton('⬅️ Back', callback_data='go_home'))

    bot.edit_message_text(
        f"👤 *My Account*\n\n"
        f"🔗 TG Name: *{tg_name}*\n"
        f"🔗 Username: {tg_user}\n"
        f"🆔 Bot ID: `{user_id}`\n\n"
        f"📦 Plan: *{plan_name}*\n"
        f"📅 Started: *{started}*\n"
        f"📅 Expires: *{expires}*\n"
        f"⏳ Remaining: `{countdown}`\n"
        f"🔰 Status: {status}\n\n"
        f"📥 Source: `{source}`\n"
        f"📤 Active Targets: *{targets} channels*",
        call.message.chat.id, call.message.message_id,
        reply_markup=markup, parse_mode='Markdown'
    )

# ==================== SOURCE CHANNEL ====================
@bot.callback_query_handler(func=lambda c: c.data == 'set_source')
def set_source(call):
    if not is_subscribed(call.message.chat.id):
        bot.answer_callback_query(call.id, "❌ Please get a plan first!")
        return
    bot.answer_callback_query(call.id)
    markup = types.InlineKeyboardMarkup()
    markup.row(types.InlineKeyboardButton('📋 Pick from My Channels', callback_data='pick_source'))

    bot.send_message(
        call.message.chat.id,
        "📥 *Send Source Channel ID:*\n\n"
        "Example: `-1001234567890`\n\n"
        "💡 Or pick from the channel list below",
        reply_markup=markup, parse_mode='Markdown'
    )
    bot.register_next_step_handler_by_chat_id(call.message.chat.id, save_source)

@bot.callback_query_handler(func=lambda c: c.data == 'pick_source')
def pick_source(call):
    user_id = call.message.chat.id
    sess = get_session(user_id)
    if not sess:
        bot.answer_callback_query(call.id, "❌ Please connect your account first!")
        return
    bot.answer_callback_query(call.id)
    bot.send_message(user_id, "⏳ Loading channels...")
    try:
        channels = run_async(get_user_channels(user_id))
        if not channels:
            bot.send_message(user_id, "❌ No channels found!")
            return
        markup = types.InlineKeyboardMarkup()
        for ch in channels[:20]:
            ch_id = to_channel_id(ch['id'])  # ✅ FIXED
            markup.row(types.InlineKeyboardButton(
                f"{'📢' if ch['type']=='Channel' else '👥'} {ch['name'][:30]}",
                callback_data=f"src_{ch_id}"
            ))
        markup.row(types.InlineKeyboardButton('⬅️ Back', callback_data='go_home'))
        bot.send_message(user_id, "📥 Select source channel:", reply_markup=markup)
    except Exception as e:
        bot.send_message(user_id, f"❌ Error: {str(e)}")

@bot.callback_query_handler(func=lambda c: c.data.startswith('src_'))
def select_source(call):
    user_id = call.message.chat.id
    ch_id = call.data.replace('src_', '')
    update_filter(user_id, source_channel=ch_id)
    bot.edit_message_text(
        f"✅ Source set: `{ch_id}`\n\n🔄 Restarting listener...",
        call.message.chat.id, call.message.message_id,
        parse_mode='Markdown'
    )
    _restart_listener(user_id)
    show_main_menu(user_id)

def save_source(message):
    user_id = message.from_user.id
    text = message.text.strip()
    if not text.startswith('-100'):
        bot.send_message(user_id, "❌ Invalid! ID must start with `-100`.", parse_mode='Markdown')
        return
    update_filter(user_id, source_channel=text)
    bot.send_message(user_id, f"✅ Source set: `{text}`\n\n🔄 Restarting listener...", parse_mode='Markdown')
    _restart_listener(user_id)
    show_main_menu(user_id)

# ==================== TARGET CHANNELS ====================
@bot.callback_query_handler(func=lambda c: c.data == 'set_targets')
def set_targets(call):
    if not is_subscribed(call.message.chat.id):
        bot.answer_callback_query(call.id, "❌ Please get a plan first!")
        return
    user_id = call.message.chat.id
    plan = get_user_plan(user_id)
    max_t = plan['max_targets'] if plan else 5
    bot.answer_callback_query(call.id)

    markup = types.InlineKeyboardMarkup()
    markup.row(types.InlineKeyboardButton('📋 Pick from My Channels', callback_data='pick_targets'))

    msg = bot.send_message(
        user_id,
        f"📤 Send target channel IDs (comma separated):\n\n"
        f"Your plan allows max *{max_t}* channels\n\n"
        f"Example: `-1001111,-1002222,-1003333`\n\n"
        f"Or pick from the list below 👇",
        reply_markup=markup, parse_mode='Markdown'
    )
    bot.register_next_step_handler(msg, save_targets)

@bot.callback_query_handler(func=lambda c: c.data == 'pick_targets')
def pick_targets(call):
    user_id = call.message.chat.id
    sess = get_session(user_id)
    if not sess:
        bot.answer_callback_query(call.id, "❌ Please connect your account first!")
        return
    bot.answer_callback_query(call.id)
    bot.send_message(user_id, "⏳ Loading channels...")

    try:
        channels = run_async(get_user_channels(user_id))
        if not channels:
            bot.send_message(user_id, "❌ No channels found!")
            return

        # Store selection state in dedicated dict ✅ FIXED
        pending_target_selection[user_id] = []

        markup = types.InlineKeyboardMarkup()
        for ch in channels[:20]:
            ch_id = to_channel_id(ch['id'])  # ✅ FIXED
            markup.row(types.InlineKeyboardButton(
                f"{'📢' if ch['type']=='Channel' else '👥'} {ch['name'][:30]}",
                callback_data=f"tgt_{ch_id}"
            ))
        markup.row(types.InlineKeyboardButton('✅ Done', callback_data='targets_done'))
        markup.row(types.InlineKeyboardButton('⬅️ Back', callback_data='go_home'))
        bot.send_message(user_id, "📤 Select target channels (multiple):", reply_markup=markup)
    except Exception as e:
        bot.send_message(user_id, f"❌ Error: {str(e)}")

@bot.callback_query_handler(func=lambda c: c.data.startswith('tgt_'))
def select_target(call):
    user_id = call.message.chat.id
    ch_id = call.data.replace('tgt_', '')

    if user_id not in pending_target_selection:
        pending_target_selection[user_id] = []

    if ch_id in pending_target_selection[user_id]:
        pending_target_selection[user_id].remove(ch_id)
        bot.answer_callback_query(call.id, f"❌ Removed")
    else:
        plan = get_user_plan(user_id)
        max_t = plan['max_targets'] if plan else 5
        if len(pending_target_selection[user_id]) >= max_t:
            bot.answer_callback_query(call.id, f"❌ Maximum {max_t} channels!")
            return
        pending_target_selection[user_id].append(ch_id)
        bot.answer_callback_query(call.id, f"✅ Added! ({len(pending_target_selection[user_id])} selected)")

@bot.callback_query_handler(func=lambda c: c.data == 'targets_done')
def targets_done(call):
    user_id = call.message.chat.id
    selected = pending_target_selection.get(user_id, [])

    if not selected:
        bot.answer_callback_query(call.id, "❌ No channel selected!")
        return

    update_filter(user_id, target_channels=','.join(selected))
    pending_target_selection.pop(user_id, None)

    bot.edit_message_text(
        f"✅ *{len(selected)}* target channel(s) set!",
        call.message.chat.id, call.message.message_id,
        parse_mode='Markdown'
    )
    show_main_menu(user_id)

def save_targets(message):
    user_id = message.from_user.id
    plan = get_user_plan(user_id)
    max_t = plan['max_targets'] if plan else 5
    channels = [c.strip() for c in message.text.split(',')]
    if len(channels) > max_t:
        bot.send_message(
            user_id,
            f"❌ Your plan allows max *{max_t}* channels!",
            parse_mode='Markdown'
        )
        return
    invalid = [c for c in channels if not c.startswith('-100')]
    if invalid:
        bot.send_message(user_id, f"❌ Invalid IDs: {', '.join(invalid)}")
        return
    update_filter(user_id, target_channels=','.join(channels))
    bot.send_message(user_id, f"✅ {len(channels)} target channel(s) set!")
    show_main_menu(user_id)

# ==================== WATERMARK ====================
@bot.callback_query_handler(func=lambda c: c.data == 'set_watermark')
def set_watermark(call):
    user_id = call.message.chat.id
    if not is_subscribed(user_id):
        bot.answer_callback_query(call.id, "❌ Please get a plan first!")
        return
    if not can_use_feature(user_id, 'watermark'):
        bot.answer_callback_query(call.id)
        markup = types.InlineKeyboardMarkup()
        markup.row(types.InlineKeyboardButton('💰 Upgrade Now', callback_data='show_plans'))
        markup.row(types.InlineKeyboardButton('⬅️ Back', callback_data='go_home'))
        bot.send_message(
            user_id,
            "❌ *Access Denied*\n\n"
            "This feature is only available on\n"
            "🚀 *Pro* or 💎 *Ultra* plan.\n\n"
            "Upgrade your plan to unlock this feature.",
            parse_mode='Markdown', reply_markup=markup
        )
        return
    bot.answer_callback_query(call.id)
    msg = bot.send_message(
        user_id,
        "💧 *Send Watermark text:*\n\nExample: `© My Channel @username`\n\nSend `off` to disable",
        parse_mode='Markdown'
    )
    bot.register_next_step_handler(msg, save_watermark)

def save_watermark(message):
    user_id = message.from_user.id
    text = '' if message.text.strip().lower() == 'off' else message.text.strip()
    update_filter(user_id, watermark=text)
    status = "Disabled ✅" if not text else f"`{text}` set ✅"
    bot.send_message(user_id, f"💧 Watermark {status}", parse_mode='Markdown')
    show_main_menu(user_id)

# ==================== TEXT REPLACE ====================
@bot.callback_query_handler(func=lambda c: c.data == 'set_replace')
def set_replace(call):
    user_id = call.message.chat.id
    if not is_subscribed(user_id):
        bot.answer_callback_query(call.id, "❌ Please get a plan first!")
        return
    if not can_use_feature(user_id, 'text_replace'):
        bot.answer_callback_query(call.id)
        markup = types.InlineKeyboardMarkup()
        markup.row(types.InlineKeyboardButton('💰 Upgrade Now', callback_data='show_plans'))
        markup.row(types.InlineKeyboardButton('⬅️ Back', callback_data='go_home'))
        bot.send_message(
            user_id,
            "❌ *Access Denied*\n\n"
            "This feature is only available on\n"
            "🚀 *Pro* or 💎 *Ultra* plan.\n\n"
            "Upgrade your plan to unlock this feature.",
            parse_mode='Markdown', reply_markup=markup
        )
        return
    bot.answer_callback_query(call.id)
    msg = bot.send_message(
        user_id,
        "🔤 *Set Text Replace:*\n\n"
        "Format: `old|new`\n"
        "Example: `@oldchannel|@newchannel`\n\n"
        "Send `off` to disable",
        parse_mode='Markdown'
    )
    bot.register_next_step_handler(msg, save_replace)

def save_replace(message):
    user_id = message.from_user.id
    text = '' if message.text.strip().lower() == 'off' else message.text.strip()
    update_filter(user_id, text_replace=text)
    bot.send_message(user_id, "✅ Text Replace updated!")
    show_main_menu(user_id)

# ==================== FILTER MENU ====================
@bot.callback_query_handler(func=lambda c: c.data == 'filter_menu')
def filter_menu(call):
    if not is_subscribed(call.message.chat.id):
        bot.answer_callback_query(call.id, "❌ Please get a plan first!")
        return
    show_filter_menu(call.message.chat.id, call.message.message_id)

def show_filter_menu(chat_id, msg_id):
    f = get_filter(chat_id)
    if not f:
        update_filter(chat_id)
        f = get_filter(chat_id)

    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton(f"📝 Text {'✅' if f[6] else '❌'}", callback_data='toggle_text'),
        types.InlineKeyboardButton(f"🖼️ Photo {'✅' if f[7] else '❌'}", callback_data='toggle_photo')
    )
    markup.row(
        types.InlineKeyboardButton(f"🎥 Video {'✅' if f[8] else '❌'}", callback_data='toggle_video'),
        types.InlineKeyboardButton(f"📄 Doc {'✅' if f[9] else '❌'}", callback_data='toggle_document')
    )
    markup.row(
        types.InlineKeyboardButton(f"🎵 Audio {'✅' if f[10] else '❌'}", callback_data='toggle_audio'),
        types.InlineKeyboardButton(f"🎭 Sticker {'✅' if f[11] else '❌'}", callback_data='toggle_sticker')
    )
    markup.row(types.InlineKeyboardButton('⬅️ Back', callback_data='go_home'))

    try:
        bot.edit_message_text(
            "🎛️ *Media Filters*\n\nSelect content types to forward:",
            chat_id, msg_id, reply_markup=markup, parse_mode='Markdown'
        )
    except:
        pass

# ==================== SETTINGS MENU ====================
@bot.callback_query_handler(func=lambda c: c.data == 'settings_menu')
def settings_menu(call):
    if not is_subscribed(call.message.chat.id):
        bot.answer_callback_query(call.id, "❌ Please get a plan first!")
        return
    show_settings_menu(call.message.chat.id, call.message.message_id)

def show_settings_menu(chat_id, msg_id):
    f = get_filter(chat_id)
    if not f:
        update_filter(chat_id)
        f = get_filter(chat_id)

    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton(f"🔇 Silent {'✅' if f[4] else '❌'}", callback_data='toggle_silent'),  # f[4]=silent_mode ✅
        types.InlineKeyboardButton(f"{'▶️ Resume' if f[5] else '⏸️ Pause'}", callback_data='toggle_pause')  # f[5]=paused ✅
    )
    markup.row(types.InlineKeyboardButton('🗑️ Auto Delete Timer', callback_data='set_autodelete'))
    markup.row(types.InlineKeyboardButton('⬅️ Back', callback_data='go_home'))

    try:
        bot.edit_message_text(
            "⚙️ *Settings*\n\n"
            f"🔇 Silent Mode: *{'Enabled ✅' if f[4] else 'Disabled ❌'}*\n"   # f[4]=silent_mode ✅
            f"⏸️ Forwarding: *{'Paused ⏸️' if f[5] else 'Active ▶️'}*\n"       # f[5]=paused ✅
            f"🗑️ Auto Delete: *{f[13]} mins {'(off)' if f[13] == 0 else ''}*",
            chat_id, msg_id, reply_markup=markup, parse_mode='Markdown'
        )
    except:
        pass

@bot.callback_query_handler(func=lambda c: c.data.startswith('toggle_'))
def toggle_handler(call):
    user_id = call.message.chat.id
    f = get_filter(user_id)

    field_map = {
        'toggle_text': ('allow_text', 6),
        'toggle_photo': ('allow_photo', 7),
        'toggle_video': ('allow_video', 8),
        'toggle_document': ('allow_document', 9),
        'toggle_audio': ('allow_audio', 10),
        'toggle_sticker': ('allow_sticker', 11),
        'toggle_silent': ('silent_mode', 4),   # f[4]=silent_mode ✅ FIXED
        'toggle_pause': ('paused', 5)           # f[5]=paused ✅ FIXED
    }

    if call.data not in field_map:
        return

    field, idx = field_map[call.data]
    update_filter(user_id, **{field: 0 if f[idx] else 1})

    if call.data in ['toggle_silent', 'toggle_pause']:
        show_settings_menu(user_id, call.message.message_id)
    else:
        show_filter_menu(user_id, call.message.message_id)

@bot.callback_query_handler(func=lambda c: c.data == 'set_autodelete')
def set_autodelete(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(
        call.message.chat.id,
        "🗑️ After how many minutes should messages be deleted?\n\nExample: `10`\nSend `0` to disable",
        parse_mode='Markdown'
    )
    bot.register_next_step_handler(msg, save_autodelete)

def save_autodelete(message):
    user_id = message.from_user.id
    try:
        mins = int(message.text.strip())
        update_filter(user_id, auto_delete_mins=mins)
        status = "Disabled ✅" if mins == 0 else f"{mins} min(s) ✅"
        bot.send_message(user_id, f"🗑️ Auto Delete: {status}")
    except:
        bot.send_message(user_id, "❌ Please enter a number!")
    show_main_menu(user_id)

# ==================== STATS ====================
@bot.callback_query_handler(func=lambda c: c.data == 'show_stats')
def show_stats(call):
    user_id = call.message.chat.id
    conn = sqlite3.connect('voidbridge.db')
    c = conn.cursor()
    c.execute('INSERT OR IGNORE INTO stats (user_id) VALUES (?)', (user_id,))
    c.execute('SELECT total_forwarded, total_edited, total_deleted FROM stats WHERE user_id = ?', (user_id,))
    row = c.fetchone()
    conn.commit()
    conn.close()

    fwd, edt, dlt = row if row else (0, 0, 0)
    f = get_filter(user_id)
    source = f[1] if f and f[1] else 'Not set'
    targets = len(f[2].split(',')) if f and f[2] else 0
    countdown = get_expiry_countdown(user_id)

    markup = types.InlineKeyboardMarkup()
    markup.row(types.InlineKeyboardButton('🔄 Refresh', callback_data='show_stats'))
    markup.row(types.InlineKeyboardButton('⬅️ Back', callback_data='go_home'))

    bot.edit_message_text(
        f"📊 *Statistics*\n\n"
        f"📨 Total Forwarded: *{fwd}*\n"
        f"✏️ Total Edited: *{edt}*\n"
        f"🗑️ Total Deleted: *{dlt}*\n\n"
        f"📥 Source: `{source}`\n"
        f"📤 Active Targets: *{targets}*\n\n"
        f"⏳ Plan Expires In: `{countdown}`",
        call.message.chat.id, call.message.message_id,
        reply_markup=markup, parse_mode='Markdown'
    )

# ==================== GO HOME ====================
@bot.callback_query_handler(func=lambda c: c.data == 'go_home')
def go_home(call):
    bot.answer_callback_query(call.id)
    show_main_menu(call.message.chat.id, call.message.message_id)


# ==================== LISTENER RESTART HELPER ====================
# Per-user stop events so we can signal the correct listener loop
active_listener_loops = {}   # user_id -> asyncio event loop of that listener thread

# ==================== FORWARD ENGINE ====================
# user_id -> asyncio loop of that user's listener thread
active_listener_loops = {}
# user_id -> threading.Thread
active_listeners = {}
# Album/media group buffer: (user_id, grouped_id) -> [messages]
album_buffer = {}
album_timers = {}

def stop_listener(user_id):
    """Stop existing listener for a user cleanly."""
    loop = active_listener_loops.get(user_id)
    client = active_clients.get(user_id)
    if loop and client:
        try:
            asyncio.run_coroutine_threadsafe(client.disconnect(), loop)
        except Exception:
            pass
    active_clients.pop(user_id, None)
    active_listener_loops.pop(user_id, None)
    time.sleep(1.5)

def _restart_listener(user_id):
    stop_listener(user_id)
    start_user_listener(user_id)

def start_user_listener(user_id):
    """Start a fresh listener thread for a user."""

    # Kill old listener if running
    if user_id in active_listeners and active_listeners[user_id].is_alive():
        stop_listener(user_id)

    def _run():
        my_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(my_loop)
        active_listener_loops[user_id] = my_loop
        try:
            my_loop.run_until_complete(_listen(my_loop))
        except Exception as e:
            print(f"[{user_id}] Listener thread error: {e}")
        finally:
            my_loop.close()
            active_listeners.pop(user_id, None)
            active_listener_loops.pop(user_id, None)
            active_clients.pop(user_id, None)
            print(f"[{user_id}] Listener stopped")

    async def _listen(my_loop):
        client = None
        try:
            sess = get_session(user_id)
            if not sess:
                print(f"[{user_id}] No session found")
                return

            client = TelegramClient(StringSession(sess[2]), API_ID, API_HASH, loop=my_loop)
            await client.connect()

            if not await client.is_user_authorized():
                print(f"[{user_id}] Not authorized")
                return

            active_clients[user_id] = client
            print(f"[{user_id}] Connected to Telegram ✅")

            f = get_filter(user_id)
            if not f or not f[1]:
                print(f"[{user_id}] No source channel set, waiting for source...")
                # Notify user
                try:
                    bot.send_message(user_id,
                        "✅ *Bot Connected! Forwarding is now active.*\n\n"
                        "⚠️ Please set a *Source Channel* to start forwarding.",
                        parse_mode='Markdown')
                except Exception:
                    pass
                await client.run_until_disconnected()
                return

            source_id = int(f[1])
            print(f"[{user_id}] Listening → source {source_id}")

            # Notify user bot is ready
            try:
                bot.send_message(user_id,
                    "✅ *Bot Connected! Forwarding is now active.*",
                    parse_mode='Markdown')
            except Exception:
                pass

            @client.on(events.NewMessage(chats=source_id))
            async def on_new(event):
                msg = event.message
                if msg.grouped_id:
                    key = (user_id, msg.grouped_id)
                    if key not in album_buffer:
                        album_buffer[key] = []
                    album_buffer[key].append(msg)
                    if key in album_timers:
                        album_timers[key].cancel()
                    async def flush_album(k):
                        await asyncio.sleep(0.8)
                        msgs = album_buffer.pop(k, [])
                        album_timers.pop(k, None)
                        if msgs:
                            msgs.sort(key=lambda m: m.id)
                            await _forward_album(k[0], client, msgs, my_loop)
                    task = asyncio.ensure_future(flush_album(key))
                    album_timers[key] = task
                else:
                    await _forward(user_id, client, msg, my_loop)

            @client.on(events.MessageEdited(chats=source_id))
            async def on_edit(event):
                await _edit(user_id, client, event.message)

            await client.run_until_disconnected()

        except Exception as e:
            print(f"[{user_id}] Listener error: {e}")
            import traceback; traceback.print_exc()
        finally:
            if client:
                try:
                    await client.disconnect()
                except Exception:
                    pass

    t = threading.Thread(target=_run, daemon=True, name=f"listener_{user_id}")
    t.start()
    active_listeners[user_id] = t
    print(f"[{user_id}] Listener thread started")


async def _forward_album(user_id, client, msgs, my_loop):
    """Forward a media group (album) to all target channels."""
    try:
        f = get_filter(user_id)
        if not f or not f[2]:
            return
        if f[5]:  # paused
            return

        if user_id != ADMIN_ID:
            user = get_user(user_id)
            if not user or not user[4]:
                return
            if datetime.fromisoformat(user[4]) < datetime.now():
                return
            plan_key = user[2]
        else:
            user = get_user(user_id)
            plan_key = user[2] if user else None

        plan      = PLANS.get(plan_key, {})
        targets   = [t.strip() for t in f[2].split(',') if t.strip()]
        watermark = f[3] or ''
        silent    = bool(f[4])
        txt_rep   = f[12] or ''
        wm        = f"\n\n{watermark}" if (watermark and plan.get('watermark')) else ''

        for ch in targets:
            try:
                ch_id = int(ch)
                files = []
                captions = []
                for msg in msgs:
                    if msg.media:
                        cap = apply_replace(msg.message or '', txt_rep if plan.get('text_replace') else '')
                        captions.append(cap)
                        if msg.photo and watermark and plan.get('watermark'):
                            try:
                                img_bytes = await client.download_media(msg.media, bytes)
                                wm_bytes = add_watermark_to_image(img_bytes, watermark)
                                files.append(io.BytesIO(wm_bytes))
                            except Exception:
                                files.append(msg.media)
                        else:
                            files.append(msg.media)

                if not files:
                    continue

                # Add watermark text to last caption only
                if captions:
                    captions[-1] = captions[-1] + wm

                sent_list = await client.send_file(
                    ch_id,
                    files,
                    caption=captions,
                    silent=silent
                )

                if sent_list:
                    if not isinstance(sent_list, list):
                        sent_list = [sent_list]
                    db = sqlite3.connect('voidbridge.db')
                    for i, sent in enumerate(sent_list):
                        db.execute(
                            'INSERT OR REPLACE INTO msg_map VALUES (?, ?, ?, ?, ?)',
                            (msgs[i].id, user_id, ch, sent.id, datetime.now().isoformat())
                        )
                    db.commit()
                    db.close()
                    update_stats(user_id, 'total_forwarded')
                    print(f"[{user_id}] Forwarded album ({len(files)} files) → {ch_id}")

            except Exception as e:
                print(f"[{user_id}] Album forward error → {ch}: {e}")

    except Exception as e:
        print(f"[{user_id}] _forward_album error: {e}")


async def _forward(user_id, client, msg, my_loop):
    """Forward a single message to all target channels."""
    try:
        f = get_filter(user_id)
        if not f or not f[2]:
            return
        # f[5] = paused
        if f[5]:
            return

        # Subscription check — admin always passes
        if user_id != ADMIN_ID:
            user = get_user(user_id)
            if not user or not user[4]:
                return
            if datetime.fromisoformat(user[4]) < datetime.now():
                return
            plan_key = user[2]
        else:
            user = get_user(user_id)
            plan_key = user[2] if user else None

        plan      = PLANS.get(plan_key, {})
        targets   = [t.strip() for t in f[2].split(',') if t.strip()]
        watermark = f[3] or ''
        silent    = bool(f[4])   # f[4] = silent_mode
        a_text    = f[6]
        a_photo   = f[7]
        a_video   = f[8]
        a_doc     = f[9]
        a_audio   = f[10]
        a_sticker = f[11]
        txt_rep   = f[12] or ''
        auto_del  = f[13] or 0
        wm        = f"\n\n{watermark}" if (watermark and plan.get('watermark')) else ''

        for ch in targets:
            try:
                ch_id = int(ch)
                sent  = None

                if msg.text and a_text:
                    text = apply_replace(msg.text, txt_rep if plan.get('text_replace') else '') + wm
                    sent = await client.send_message(ch_id, text, silent=silent)

                elif msg.media:
                    cap = apply_replace(msg.message or '', txt_rep if plan.get('text_replace') else '') + wm
                    if msg.photo and a_photo:
                        if watermark and plan.get('watermark'):
                            try:
                                img_bytes = await client.download_media(msg.media, bytes)
                                wm_bytes = add_watermark_to_image(img_bytes, watermark)
                                sent = await client.send_file(ch_id, io.BytesIO(wm_bytes), caption=cap, silent=silent)
                            except Exception:
                                sent = await client.send_file(ch_id, msg.media, caption=cap, silent=silent)
                        else:
                            sent = await client.send_file(ch_id, msg.media, caption=cap, silent=silent)
                    elif msg.video and a_video:
                        sent = await client.send_file(ch_id, msg.media, caption=cap, silent=silent)
                    elif msg.document and a_doc:
                        sent = await client.send_file(ch_id, msg.media, caption=cap, silent=silent)
                    elif msg.audio and a_audio:
                        sent = await client.send_file(ch_id, msg.media, caption=cap, silent=silent)
                    elif msg.sticker and a_sticker:
                        sent = await client.send_file(ch_id, msg.media, silent=silent)

                if sent:
                    db = sqlite3.connect('voidbridge.db')
                    db.execute(
                        'INSERT OR REPLACE INTO msg_map VALUES (?, ?, ?, ?, ?)',
                        (msg.id, user_id, ch, sent.id, datetime.now().isoformat())
                    )
                    db.commit()
                    db.close()
                    update_stats(user_id, 'total_forwarded')
                    print(f"[{user_id}] Forwarded msg {msg.id} → {ch_id}")

                    if auto_del > 0:
                        async def _del(cid, mid, delay, uid, cl):
                            await asyncio.sleep(delay * 60)
                            try:
                                await cl.delete_messages(cid, mid)
                                update_stats(uid, 'total_deleted')
                            except Exception:
                                pass
                        asyncio.ensure_future(_del(ch_id, sent.id, auto_del, user_id, client))

            except Exception as e:
                print(f"[{user_id}] Forward error → {ch}: {e}")

    except Exception as e:
        print(f"[{user_id}] _forward error: {e}")


async def _edit(user_id, client, msg):
    """Sync edits to all target channels."""
    try:
        f = get_filter(user_id)
        if not f:
            return
        user = get_user(user_id)
        plan = PLANS.get(user[2] if user else None, {})
        wm = f"\n\n{f[3]}" if (f[3] and plan.get('watermark')) else ''
        txt_rep = f[12] or ''

        conn = sqlite3.connect('voidbridge.db')
        c = conn.cursor()
        c.execute('SELECT channel_id, sent_msg_id FROM msg_map WHERE source_msg_id=? AND user_id=?',
                  (msg.id, user_id))
        rows = c.fetchall()
        conn.close()

        for channel_id, sent_msg_id in rows:
            try:
                new_text = apply_replace(msg.text or msg.message or '',
                                         txt_rep if plan.get('text_replace') else '') + wm
                await client.edit_message(int(channel_id), sent_msg_id, new_text)
                update_stats(user_id, 'total_edited')
            except Exception as e:
                print(f"[{user_id}] Edit error: {e}")
    except Exception as e:
        print(f"[{user_id}] _edit error: {e}")

# ==================== LOAD EXISTING SESSIONS ====================
def load_all_sessions():
    conn = sqlite3.connect('voidbridge.db')
    c = conn.cursor()
    c.execute('SELECT user_id FROM sessions')
    users = c.fetchall()
    conn.close()

    for u in users:
        user_id = u[0]
        threading.Thread(
            target=start_user_listener,
            args=(user_id,),
            daemon=True
        ).start()
        time.sleep(0.5)

# ==================== ADMIN PANEL ====================
@bot.message_handler(commands=['admin'])
def admin_panel(message):
    if message.from_user.id != ADMIN_ID:
        return

    conn = sqlite3.connect('voidbridge.db')
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM users')
    total = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM users WHERE expires_at > ?", (datetime.now().isoformat(),))
    active = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM sessions")
    connected = c.fetchone()[0]
    c.execute("SELECT SUM(total_forwarded) FROM stats")
    total_fwd = c.fetchone()[0] or 0
    c.execute("SELECT COUNT(*) FROM users WHERE plan = 'free' AND expires_at > ?", (datetime.now().isoformat(),))
    free_count = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM users WHERE plan = 'basic' AND expires_at > ?", (datetime.now().isoformat(),))
    basic_count = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM users WHERE plan = 'pro' AND expires_at > ?", (datetime.now().isoformat(),))
    pro_count = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM users WHERE plan = 'ultra' AND expires_at > ?", (datetime.now().isoformat(),))
    ultra_count = c.fetchone()[0]
    conn.close()

    markup = types.InlineKeyboardMarkup()
    markup.row(types.InlineKeyboardButton('📢 Broadcast', callback_data='admin_broadcast'))
    markup.row(types.InlineKeyboardButton('👥 User List', callback_data='admin_users'))
    markup.row(types.InlineKeyboardButton('👤 Grant Plan', callback_data='admin_grant'))

    bot.send_message(
        ADMIN_ID,
        f"🔐 *Admin Panel*\n\n"
        f"👥 Total Users: *{total}*\n"
        f"✅ Active Subscribers: *{active}*\n"
        f"🔗 Connected Accounts: *{connected}*\n"
        f"📨 Total Forwarded: *{total_fwd}*\n\n"
        f"📊 *Plan Breakdown:*\n"
        f"🆓 Free Trial: *{free_count}*\n"
        f"⭐ Basic: *{basic_count}*\n"
        f"🚀 Pro: *{pro_count}*\n"
        f"💎 Ultra: *{ultra_count}*",
        reply_markup=markup, parse_mode='Markdown'
    )

@bot.callback_query_handler(func=lambda c: c.data == 'admin_grant')
def admin_grant(call):
    if call.from_user.id != ADMIN_ID:
        return
    msg = bot.send_message(
        ADMIN_ID,
        "👤 Grant Plan:\n\nFormat: `user_id plan_key days`\nExample: `123456789 pro 30`"
    )
    bot.register_next_step_handler(msg, do_grant)

def do_grant(message):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        parts = message.text.strip().split()
        uid = int(parts[0])
        plan_key = parts[1]
        days = int(parts[2])

        if plan_key not in PLANS:
            bot.send_message(ADMIN_ID, "❌ Invalid plan!")
            return

        now = datetime.now()
        expires = (now + timedelta(days=days)).isoformat()
        update_user_field(uid, plan=plan_key, started_at=now.isoformat(), expires_at=expires)

        bot.send_message(ADMIN_ID, f"✅ Granted *{plan_key}* to `{uid}` for {days} days!", parse_mode='Markdown')
        bot.send_message(
            uid,
            f"🎉 *Plan Activated!*\n\nPlan: *{PLANS[plan_key]['name']}*\nExpires: *{expires[:10]}*",
            parse_mode='Markdown'
        )
    except Exception as e:
        bot.send_message(ADMIN_ID, f"❌ Error: {e}\n\nFormat: `user_id plan_key days`")

@bot.callback_query_handler(func=lambda c: c.data == 'admin_broadcast')
def admin_broadcast(call):
    if call.from_user.id != ADMIN_ID:
        return
    msg = bot.send_message(ADMIN_ID, "📢 Write your broadcast message:")
    bot.register_next_step_handler(msg, do_broadcast)

def do_broadcast(message):
    if message.from_user.id != ADMIN_ID:
        return
    conn = sqlite3.connect('voidbridge.db')
    c = conn.cursor()
    c.execute('SELECT user_id FROM users')
    users = c.fetchall()
    conn.close()

    success = 0
    for u in users:
        try:
            bot.send_message(u[0], f"📢 *Announcement:*\n\n{message.text}", parse_mode='Markdown')
            success += 1
            time.sleep(0.05)
        except:
            pass
    bot.send_message(ADMIN_ID, f"✅ Sent to {success}/{len(users)} users!")

@bot.callback_query_handler(func=lambda c: c.data == 'admin_users')
def admin_users(call):
    if call.from_user.id != ADMIN_ID:
        return
    conn = sqlite3.connect('voidbridge.db')
    c = conn.cursor()
    c.execute('SELECT user_id, username, plan, expires_at FROM users ORDER BY created_at DESC LIMIT 20')
    users = c.fetchall()
    conn.close()

    text = "👥 *Recent Users:*\n\n"
    for u in users:
        uid, uname, plan, exp = u
        active = exp and datetime.fromisoformat(exp) > datetime.now()
        status = "✅" if active else "❌"
        text += f"{status} `{uid}` @{uname or 'N/A'} — {plan or 'No plan'}\n"

    bot.send_message(ADMIN_ID, text, parse_mode='Markdown')

# ==================== RUN ====================
print("✅ VoidBridge Bot starting...")
load_all_sessions()
print("✅ Sessions loaded!")
bot.infinity_polling(timeout=60, long_polling_timeout=60)
