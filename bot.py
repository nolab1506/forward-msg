import os
import sqlite3
import time
import threading
import telebot
from telebot import types
from datetime import datetime, timedelta

# ==================== CONFIG ====================
BOT_TOKEN = os.environ.get('BOT_TOKEN')
ADMIN_ID = int(os.environ.get('ADMIN_ID'))

bot = telebot.TeleBot(BOT_TOKEN)

PLANS = {
    'basic': {'name': '⭐ Basic', 'price': 2, 'max_targets': 5},
    'pro': {'name': '🚀 Pro', 'price': 4, 'max_targets': 10},
    'premium': {'name': '💎 Premium', 'price': 7, 'max_targets': 999}
}

# ==================== DATABASE ====================
def init_db():
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()

    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        plan TEXT DEFAULT NULL,
        expires_at TEXT DEFAULT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
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
        status TEXT DEFAULT 'pending',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')

    conn.commit()
    conn.close()

init_db()

# ==================== DB HELPERS ====================
def get_user(user_id):
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
    row = c.fetchone()
    conn.close()
    return row

def save_user(user_id, username):
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute('INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)', (user_id, username))
    conn.commit()
    conn.close()

def get_filter(user_id):
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute('SELECT * FROM filters WHERE user_id = ?', (user_id,))
    row = c.fetchone()
    conn.close()
    return row

def update_filter(user_id, **kwargs):
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute('INSERT OR IGNORE INTO filters (user_id) VALUES (?)', (user_id,))
    for key, val in kwargs.items():
        c.execute(f'UPDATE filters SET {key} = ? WHERE user_id = ?', (val, user_id))
    conn.commit()
    conn.close()

def update_user_field(user_id, **kwargs):
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    for key, val in kwargs.items():
        c.execute(f'UPDATE users SET {key} = ? WHERE user_id = ?', (val, user_id))
    conn.commit()
    conn.close()

def is_subscribed(user_id):
    if user_id == ADMIN_ID:
        return True
    user = get_user(user_id)
    if not user or not user[3]:
        return False
    return datetime.fromisoformat(user[3]) > datetime.now()

def update_stats(user_id, field):
    conn = sqlite3.connect('bot.db')
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

# ==================== START ====================
@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.from_user.id
    username = message.from_user.username or 'Unknown'
    save_user(user_id, username)
    update_filter(user_id)
    show_main_menu(user_id)

def show_main_menu(chat_id, msg_id=None):
    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton('📥 Source Channel', callback_data='set_source'),
        types.InlineKeyboardButton('📤 Target Channels', callback_data='set_targets')
    )
    markup.row(
        types.InlineKeyboardButton('💧 Watermark', callback_data='set_watermark'),
        types.InlineKeyboardButton('🔤 Text Replace', callback_data='set_replace')
    )
    markup.row(
        types.InlineKeyboardButton('🎛️ Filters', callback_data='filter_menu'),
        types.InlineKeyboardButton('⚙️ Settings', callback_data='settings_menu')
    )
    markup.row(
        types.InlineKeyboardButton('📊 Stats', callback_data='show_stats'),
        types.InlineKeyboardButton('👤 My Account', callback_data='my_account')
    )
    markup.row(
        types.InlineKeyboardButton('💰 Buy Plan', callback_data='show_plans'),
        types.InlineKeyboardButton('❓ Help', callback_data='show_help')
    )

    text = (
        "╔══════════════════════╗\n"
        "║   📡 *ForwardPro Bot*   ║\n"
        "╚══════════════════════╝\n\n"
        "✅ Auto Forward\n"
        "✅ Auto Edit Sync\n"
        "✅ Silent Mode\n"
        "✅ Text Replace\n"
        "✅ Auto Delete\n"
        "✅ Media Filters\n"
        "✅ Pause/Resume\n"
        "✅ Subscription System\n\n"
        "নিচে থেকে setup করুন 👇"
    )

    try:
        if msg_id:
            bot.edit_message_text(text, chat_id, msg_id, reply_markup=markup, parse_mode='Markdown')
        else:
            bot.send_message(chat_id, text, reply_markup=markup, parse_mode='Markdown')
    except:
        bot.send_message(chat_id, text, reply_markup=markup, parse_mode='Markdown')

# ==================== PLANS ====================
@bot.callback_query_handler(func=lambda c: c.data == 'show_plans')
def show_plans(call):
    markup = types.InlineKeyboardMarkup()
    for key, plan in PLANS.items():
        markup.row(types.InlineKeyboardButton(
            f"{plan['name']} — ${plan['price']}/month ({plan['max_targets']} channels)",
            callback_data=f'buy_{key}'
        ))
    markup.row(types.InlineKeyboardButton('⬅️ Back', callback_data='go_home'))

    bot.edit_message_text(
        "💰 *Available Plans*\n\n"
        "⭐ *Basic — $2/month*\n└ 5 channels + Watermark\n\n"
        "🚀 *Pro — $4/month*\n└ 10 channels + Watermark\n\n"
        "💎 *Premium — $7/month*\n└ Unlimited channels + সব features\n\n"
        "একটি plan সিলেক্ট করুন 👇",
        call.message.chat.id, call.message.message_id,
        reply_markup=markup, parse_mode='Markdown'
    )

@bot.callback_query_handler(func=lambda c: c.data.startswith('buy_'))
def buy_plan(call):
    user_id = call.message.chat.id
    plan_key = call.data.split('_')[1]
    plan = PLANS[plan_key]

    markup = types.InlineKeyboardMarkup()
    markup.row(types.InlineKeyboardButton(
        '✅ Payment করেছি', callback_data=f'manual_verify_{plan_key}'
    ))
    markup.row(types.InlineKeyboardButton('⬅️ Back', callback_data='show_plans'))

    bot.edit_message_text(
        f"💳 *Payment Details*\n\n"
        f"Plan: *{plan['name']}*\n"
        f"Amount: *${plan['price']} USDT (TRC20)*\n\n"
        f"📋 *Wallet Address:*\n"
        f"`YOUR_USDT_TRC20_WALLET_HERE`\n\n"
        f"⚠️ শুধুমাত্র *TRC20 USDT* পাঠান\n"
        f"Payment করার পর নিচের বাটন চাপুন 👇",
        call.message.chat.id, call.message.message_id,
        reply_markup=markup, parse_mode='Markdown'
    )

@bot.callback_query_handler(func=lambda c: c.data.startswith('manual_verify_'))
def manual_verify(call):
    user_id = call.message.chat.id
    plan_key = call.data.split('_')[2]

    bot.send_message(
        user_id,
        "📋 আপনার *Transaction ID (TxID)* পাঠান:\n\n"
        "Example: `abc123def456...`",
        parse_mode='Markdown'
    )

    def get_txid(message):
        txid = message.text.strip()
        plan = PLANS[plan_key]

        # Admin কে notify করো
        markup = types.InlineKeyboardMarkup()
        markup.row(
            types.InlineKeyboardButton('✅ Approve', callback_data=f'approve_{user_id}_{plan_key}'),
            types.InlineKeyboardButton('❌ Reject', callback_data=f'reject_{user_id}')
        )
        bot.send_message(
            ADMIN_ID,
            f"💰 *New Payment Request!*\n\n"
            f"User: `{user_id}`\n"
            f"Plan: *{plan['name']}*\n"
            f"Amount: *${plan['price']}*\n"
            f"TxID: `{txid}`",
            reply_markup=markup, parse_mode='Markdown'
        )
        bot.send_message(user_id, "⏳ Payment verify হচ্ছে, অপেক্ষা করুন...")

    bot.register_next_step_handler(call.message, get_txid)

@bot.callback_query_handler(func=lambda c: c.data.startswith('approve_'))
def approve_payment(call):
    if call.from_user.id != ADMIN_ID:
        return
    parts = call.data.split('_')
    target_user = int(parts[1])
    plan_key = parts[2]
    plan = PLANS[plan_key]
    expires = (datetime.now() + timedelta(days=30)).isoformat()

    update_user_field(target_user, plan=plan_key, expires_at=expires)
    update_filter(target_user)

    bot.send_message(
        target_user,
        f"✅ *Payment Approved!*\n\n"
        f"Plan: *{plan['name']}*\n"
        f"Expires: *{expires[:10]}*\n\n"
        f"এখন /start দিয়ে bot setup করুন! 🚀",
        parse_mode='Markdown'
    )
    bot.answer_callback_query(call.id, f"✅ User {target_user} approved!")

@bot.callback_query_handler(func=lambda c: c.data.startswith('reject_'))
def reject_payment(call):
    if call.from_user.id != ADMIN_ID:
        return
    target_user = int(call.data.split('_')[1])
    bot.send_message(target_user, "❌ Payment reject হয়েছে। সমস্যা হলে support এ যোগাযোগ করুন।")
    bot.answer_callback_query(call.id, "❌ Rejected!")

# ==================== MY ACCOUNT ====================
@bot.callback_query_handler(func=lambda c: c.data == 'my_account')
def my_account(call):
    user_id = call.message.chat.id
    user = get_user(user_id)
    f = get_filter(user_id)

    plan = user[2] if user and user[2] else 'No Plan'
    expires = user[3][:10] if user and user[3] else 'N/A'
    status = "✅ Active" if is_subscribed(user_id) else "❌ Expired"
    source = f[1] if f and f[1] else 'Not set'
    targets = len(f[2].split(',')) if f and f[2] else 0
    watermark = f[3] if f and f[3] else 'Not set'

    markup = types.InlineKeyboardMarkup()
    markup.row(types.InlineKeyboardButton('💰 Buy/Upgrade Plan', callback_data='show_plans'))
    markup.row(types.InlineKeyboardButton('⬅️ Back', callback_data='go_home'))

    bot.edit_message_text(
        f"👤 *My Account*\n\n"
        f"🆔 ID: `{user_id}`\n"
        f"📦 Plan: *{plan.capitalize()}*\n"
        f"📅 Expires: *{expires}*\n"
        f"🔰 Status: {status}\n\n"
        f"📥 Source: `{source}`\n"
        f"📤 Targets: *{targets} channels*\n"
        f"💧 Watermark: `{watermark}`",
        call.message.chat.id, call.message.message_id,
        reply_markup=markup, parse_mode='Markdown'
    )

# ==================== SOURCE CHANNEL ====================
@bot.callback_query_handler(func=lambda c: c.data == 'set_source')
def set_source(call):
    if not is_subscribed(call.message.chat.id):
        bot.answer_callback_query(call.id, "❌ আগে একটি Plan কিনুন!")
        return
    bot.answer_callback_query(call.id)
    msg = bot.send_message(
        call.message.chat.id,
        "📥 *Source Channel ID* পাঠান:\n\nExample: `-1001234567890`",
        parse_mode='Markdown'
    )
    bot.register_next_step_handler(msg, save_source)

def save_source(message):
    user_id = message.from_user.id
    text = message.text.strip()
    if not text.startswith('-100'):
        bot.send_message(user_id, "❌ Invalid! `-100` দিয়ে শুরু হওয়া ID দিন।", parse_mode='Markdown')
        return
    update_filter(user_id, source_channel=text)
    bot.send_message(user_id, f"✅ Source set: `{text}`", parse_mode='Markdown')
    show_main_menu(user_id)

# ==================== TARGET CHANNELS ====================
@bot.callback_query_handler(func=lambda c: c.data == 'set_targets')
def set_targets(call):
    if not is_subscribed(call.message.chat.id):
        bot.answer_callback_query(call.id, "❌ আগে একটি Plan কিনুন!")
        return
    user_id = call.message.chat.id
    user = get_user(user_id)
    plan_key = user[2] if user and user[2] else 'basic'
    max_t = PLANS.get(plan_key, PLANS['basic'])['max_targets']

    bot.answer_callback_query(call.id)
    msg = bot.send_message(
        user_id,
        f"📤 Target Channel IDs পাঠান (comma দিয়ে আলাদা):\n\n"
        f"আপনার plan এ সর্বোচ্চ *{max_t}টি* channel\n\n"
        f"Example: `-1001111,-1002222,-1003333`",
        parse_mode='Markdown'
    )
    bot.register_next_step_handler(msg, save_targets)

def save_targets(message):
    user_id = message.from_user.id
    user = get_user(user_id)
    plan_key = user[2] if user and user[2] else 'basic'
    max_t = PLANS.get(plan_key, PLANS['basic'])['max_targets']

    channels = [c.strip() for c in message.text.split(',')]
    if len(channels) > max_t:
        bot.send_message(user_id, f"❌ আপনার plan এ সর্বোচ্চ {max_t}টি channel!")
        return
    invalid = [c for c in channels if not c.startswith('-100')]
    if invalid:
        bot.send_message(user_id, f"❌ Invalid IDs: {', '.join(invalid)}")
        return
    update_filter(user_id, target_channels=','.join(channels))
    bot.send_message(user_id, f"✅ {len(channels)}টি Target Channel set!")
    show_main_menu(user_id)

# ==================== WATERMARK ====================
@bot.callback_query_handler(func=lambda c: c.data == 'set_watermark')
def set_watermark(call):
    if not is_subscribed(call.message.chat.id):
        bot.answer_callback_query(call.id, "❌ আগে একটি Plan কিনুন!")
        return
    bot.answer_callback_query(call.id)
    msg = bot.send_message(
        call.message.chat.id,
        "💧 *Watermark* text পাঠান:\n\nExample: `© My Channel @username`\n\nবন্ধ করতে `off` পাঠান",
        parse_mode='Markdown'
    )
    bot.register_next_step_handler(msg, save_watermark)

def save_watermark(message):
    user_id = message.from_user.id
    text = '' if message.text.strip().lower() == 'off' else message.text.strip()
    update_filter(user_id, watermark=text)
    status = "বন্ধ ✅" if not text else f"`{text}` set ✅"
    bot.send_message(user_id, f"💧 Watermark {status}", parse_mode='Markdown')
    show_main_menu(user_id)

# ==================== TEXT REPLACE ====================
@bot.callback_query_handler(func=lambda c: c.data == 'set_replace')
def set_replace(call):
    if not is_subscribed(call.message.chat.id):
        bot.answer_callback_query(call.id, "❌ আগে একটি Plan কিনুন!")
        return
    bot.answer_callback_query(call.id)
    msg = bot.send_message(
        call.message.chat.id,
        "🔤 *Text Replace* সেট করুন:\n\n"
        "Format: `পুরনো|নতুন`\n"
        "Example: `@oldchannel|@newchannel`\n\n"
        "বন্ধ করতে `off` পাঠান",
        parse_mode='Markdown'
    )
    bot.register_next_step_handler(msg, save_replace)

def save_replace(message):
    user_id = message.from_user.id
    text = '' if message.text.strip().lower() == 'off' else message.text.strip()
    update_filter(user_id, text_replace=text)
    bot.send_message(user_id, "✅ Text Replace set!")
    show_main_menu(user_id)

# ==================== FILTER MENU (FIXED) ====================
@bot.callback_query_handler(func=lambda c: c.data == 'filter_menu')
def filter_menu(call):
    if not is_subscribed(call.message.chat.id):
        bot.answer_callback_query(call.id, "❌ আগে একটি Plan কিনুন!")
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
    markup.row(types.InlineKeyboardButton('⬅️ Back', callback_data='go_home'))

    try:
        bot.edit_message_text(
            "🎛️ *Media Filters*\n\nকোন ধরনের content forward হবে সেট করুন:",
            chat_id, msg_id, reply_markup=markup, parse_mode='Markdown'
        )
    except:
        pass

@bot.callback_query_handler(func=lambda c: c.data.startswith('toggle_'))
def toggle_filter(call):
    user_id = call.message.chat.id
    f = get_filter(user_id)

    field_map = {
        'toggle_text': ('allow_text', 6),
        'toggle_photo': ('allow_photo', 7),
        'toggle_video': ('allow_video', 8),
        'toggle_document': ('allow_document', 9),
        'toggle_silent': ('silent_mode', 3),
        'toggle_pause': ('paused', 4)
    }

    if call.data not in field_map:
        return

    field, idx = field_map[call.data]
    current = f[idx]
    update_filter(user_id, **{field: 0 if current else 1})

    if call.data in ['toggle_silent', 'toggle_pause']:
        show_settings_menu(user_id, call.message.message_id)
    else:
        show_filter_menu(user_id, call.message.message_id)

# ==================== SETTINGS MENU (FIXED) ====================
@bot.callback_query_handler(func=lambda c: c.data == 'settings_menu')
def settings_menu(call):
    if not is_subscribed(call.message.chat.id):
        bot.answer_callback_query(call.id, "❌ আগে একটি Plan কিনুন!")
        return
    show_settings_menu(call.message.chat.id, call.message.message_id)

def show_settings_menu(chat_id, msg_id):
    f = get_filter(chat_id)
    if not f:
        update_filter(chat_id)
        f = get_filter(chat_id)

    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton(f"🔇 Silent {'✅' if f[3] else '❌'}", callback_data='toggle_silent'),
        types.InlineKeyboardButton(f"{'▶️ Resume' if f[4] else '⏸️ Pause'}", callback_data='toggle_pause')
    )
    markup.row(types.InlineKeyboardButton('🗑️ Auto Delete Timer', callback_data='set_autodelete'))
    markup.row(types.InlineKeyboardButton('⬅️ Back', callback_data='go_home'))

    try:
        bot.edit_message_text(
            "⚙️ *Settings*\n\n"
            f"🔇 Silent Mode: *{'চালু ✅' if f[3] else 'বন্ধ ❌'}*\n"
            f"⏸️ Forwarding: *{'বন্ধ (Paused) ⏸️' if f[4] else 'চালু (Active) ▶️'}*\n"
            f"🗑️ Auto Delete: *{f[11]} mins {'(off)' if f[11] == 0 else ''}*",
            chat_id, msg_id, reply_markup=markup, parse_mode='Markdown'
        )
    except:
        pass

@bot.callback_query_handler(func=lambda c: c.data == 'set_autodelete')
def set_autodelete(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(
        call.message.chat.id,
        "🗑️ কত মিনিট পর message delete হবে?\n\nExample: `10`\nবন্ধ করতে `0` পাঠান",
        parse_mode='Markdown'
    )
    bot.register_next_step_handler(msg, save_autodelete)

def save_autodelete(message):
    user_id = message.from_user.id
    try:
        mins = int(message.text.strip())
        update_filter(user_id, auto_delete_mins=mins)
        status = "বন্ধ ✅" if mins == 0 else f"{mins} মিনিট ✅"
        bot.send_message(user_id, f"🗑️ Auto Delete: {status}")
    except:
        bot.send_message(user_id, "❌ শুধু সংখ্যা দিন!")
    show_main_menu(user_id)

# ==================== STATS (FIXED) ====================
@bot.callback_query_handler(func=lambda c: c.data == 'show_stats')
def show_stats(call):
    user_id = call.message.chat.id
    conn = sqlite3.connect('bot.db')
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

    markup = types.InlineKeyboardMarkup()
    markup.row(types.InlineKeyboardButton('⬅️ Back', callback_data='go_home'))

    bot.edit_message_text(
        f"📊 *Statistics*\n\n"
        f"📨 Total Forwarded: *{fwd}*\n"
        f"✏️ Total Edited: *{edt}*\n"
        f"🗑️ Total Deleted: *{dlt}*\n\n"
        f"📥 Source: `{source}`\n"
        f"📤 Active Targets: *{targets}*",
        call.message.chat.id, call.message.message_id,
        reply_markup=markup, parse_mode='Markdown'
    )

# ==================== HELP ====================
@bot.callback_query_handler(func=lambda c: c.data == 'show_help')
def show_help(call):
    markup = types.InlineKeyboardMarkup()
    markup.row(types.InlineKeyboardButton('⬅️ Back', callback_data='go_home'))

    bot.edit_message_text(
        "❓ *How to Use ForwardPro*\n\n"
        "1️⃣ একটি Plan কিনুন\n"
        "2️⃣ Bot কে সব channel এ *Admin* বানান\n"
        "3️⃣ Source Channel ID set করুন\n"
        "4️⃣ Target Channel IDs set করুন\n"
        "5️⃣ Watermark ও Filters set করুন\n"
        "6️⃣ Source এ post করলে সব channel এ auto forward! ✅\n\n"
        "⚠️ *Channel ID পেতে:*\n"
        "@userinfobot এ channel forward করুন\n\n"
        "🆘 Support: @yourusername",
        call.message.chat.id, call.message.message_id,
        reply_markup=markup, parse_mode='Markdown'
    )

# ==================== GO HOME ====================
@bot.callback_query_handler(func=lambda c: c.data == 'go_home')
def go_home(call):
    bot.answer_callback_query(call.id)
    show_main_menu(call.message.chat.id, call.message.message_id)

# ==================== FORWARD ENGINE ====================
@bot.channel_post_handler(func=lambda msg: True)
def handle_post(msg):
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute(
        'SELECT f.user_id, f.target_channels, f.watermark, f.silent_mode, '
        'f.allow_text, f.allow_photo, f.allow_video, f.allow_document, '
        'f.text_replace, f.auto_delete_mins '
        'FROM filters f JOIN users u ON f.user_id = u.user_id '
        'WHERE f.source_channel = ? AND f.paused = 0 '
        'AND (u.expires_at > ? OR u.user_id = ?)',
        (str(msg.chat.id), datetime.now().isoformat(), ADMIN_ID)
    )
    users = c.fetchall()
    conn.close()

    for u in users:
        (user_id, targets_str, watermark, silent,
         a_text, a_photo, a_video, a_doc,
         text_replace, auto_del) = u

        if not targets_str:
            continue

        targets = targets_str.split(',')
        wm = f"\n\n{watermark}" if watermark else ''
        kwargs = {'disable_notification': bool(silent)}

        for ch in targets:
            try:
                sent = None

                if msg.text and a_text:
                    text = apply_replace(msg.text, text_replace) + wm
                    sent = bot.send_message(int(ch), text, **kwargs)
                elif msg.photo and a_photo:
                    cap = apply_replace(msg.caption or '', text_replace) + wm
                    sent = bot.send_photo(int(ch), msg.photo[-1].file_id, caption=cap, **kwargs)
                elif msg.video and a_video:
                    cap = apply_replace(msg.caption or '', text_replace) + wm
                    sent = bot.send_video(int(ch), msg.video.file_id, caption=cap, **kwargs)
                elif msg.document and a_doc:
                    cap = apply_replace(msg.caption or '', text_replace) + wm
                    sent = bot.send_document(int(ch), msg.document.file_id, caption=cap, **kwargs)

                if sent:
                    db = sqlite3.connect('bot.db')
                    db.execute(
                        'INSERT OR REPLACE INTO msg_map VALUES (?, ?, ?, ?, ?)',
                        (msg.message_id, user_id, ch, sent.message_id, datetime.now().isoformat())
                    )
                    db.commit()
                    db.close()
                    update_stats(user_id, 'total_forwarded')

                    if auto_del and auto_del > 0:
                        def delete_later(cid, mid, delay, uid):
                            time.sleep(delay * 60)
                            try:
                                bot.delete_message(int(cid), mid)
                                update_stats(uid, 'total_deleted')
                            except:
                                pass
                        threading.Thread(
                            target=delete_later,
                            args=(ch, sent.message_id, auto_del, user_id),
                            daemon=True
                        ).start()

            except Exception as e:
                print(f"Forward error to {ch}: {e}")

# ==================== EDIT ENGINE ====================
@bot.edited_channel_post_handler(func=lambda msg: True)
def handle_edit(msg):
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute(
        'SELECT mm.channel_id, mm.sent_msg_id, f.watermark, f.text_replace, mm.user_id '
        'FROM msg_map mm JOIN filters f ON mm.user_id = f.user_id '
        'WHERE mm.source_msg_id = ? AND f.source_channel = ?',
        (msg.message_id, str(msg.chat.id))
    )
    rows = c.fetchall()
    conn.close()

    for row in rows:
        channel_id, sent_msg_id, watermark, text_replace, user_id = row
        wm = f"\n\n{watermark}" if watermark else ''

        try:
            if msg.text:
                text = apply_replace(msg.text, text_replace) + wm
                bot.edit_message_text(text, int(channel_id), sent_msg_id)
            elif msg.caption is not None:
                cap = apply_replace(msg.caption, text_replace) + wm
                bot.edit_message_caption(cap, int(channel_id), sent_msg_id)
            update_stats(user_id, 'total_edited')
        except Exception as e:
            print(f"Edit error: {e}")

# ==================== ADMIN PANEL ====================
@bot.message_handler(commands=['admin'])
def admin_panel(message):
    if message.from_user.id != ADMIN_ID:
        return

    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM users')
    total = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM users WHERE expires_at > ?", (datetime.now().isoformat(),))
    active = c.fetchone()[0]
    c.execute("SELECT SUM(total_forwarded) FROM stats")
    total_fwd = c.fetchone()[0] or 0
    conn.close()

    markup = types.InlineKeyboardMarkup()
    markup.row(types.InlineKeyboardButton('📢 Broadcast', callback_data='admin_broadcast'))
    markup.row(types.InlineKeyboardButton('👥 User List', callback_data='admin_users'))

    bot.send_message(
        ADMIN_ID,
        f"🔐 *Admin Panel*\n\n"
        f"👥 Total Users: *{total}*\n"
        f"✅ Active Subscribers: *{active}*\n"
        f"📨 Total Forwarded: *{total_fwd}*",
        reply_markup=markup, parse_mode='Markdown'
    )

@bot.callback_query_handler(func=lambda c: c.data == 'admin_broadcast')
def admin_broadcast(call):
    if call.from_user.id != ADMIN_ID:
        return
    msg = bot.send_message(ADMIN_ID, "📢 Broadcast message লিখুন:")
    bot.register_next_step_handler(msg, do_broadcast)

def do_broadcast(message):
    if message.from_user.id != ADMIN_ID:
        return
    conn = sqlite3.connect('bot.db')
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

    bot.send_message(ADMIN_ID, f"✅ {success}/{len(users)} জনকে পাঠানো হয়েছে!")

@bot.callback_query_handler(func=lambda c: c.data == 'admin_users')
def admin_users(call):
    if call.from_user.id != ADMIN_ID:
        return
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute('SELECT user_id, username, plan, expires_at FROM users ORDER BY created_at DESC LIMIT 20')
    users = c.fetchall()
    conn.close()

    text = "👥 *Recent Users (last 20):*\n\n"
    for u in users:
        uid, uname, plan, exp = u
        status = "✅" if (exp and datetime.fromisoformat(exp) > datetime.now()) else "❌"
        text += f"{status} `{uid}` @{uname or 'N/A'} — {plan or 'No plan'}\n"

    bot.send_message(ADMIN_ID, text, parse_mode='Markdown')

# ==================== RUN ====================
print("✅ ForwardPro Bot running...")
bot.infinity_polling(timeout=60, long_polling_timeout=60)
