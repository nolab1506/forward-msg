import os
import sqlite3
import time
import telebot
from telebot import types
from datetime import datetime

# ==================== CONFIG ====================
BOT_TOKEN = os.environ.get('BOT_TOKEN')
ADMIN_ID = int(os.environ.get('ADMIN_ID'))

bot = telebot.TeleBot(BOT_TOKEN)

# ==================== DATABASE ====================
def init_db():
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()

    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS filters (
        user_id INTEGER PRIMARY KEY,
        source_channel TEXT,
        target_channels TEXT,
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

    conn.commit()
    conn.close()

init_db()

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

def update_stats(user_id, field):
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute('INSERT OR IGNORE INTO stats (user_id) VALUES (?)', (user_id,))
    c.execute(f'UPDATE stats SET {field} = {field} + 1 WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

# ==================== START ====================
@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.from_user.id
    username = message.from_user.username or 'Unknown'

    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute('INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)', (user_id, username))
    conn.commit()
    conn.close()

    update_filter(user_id)
    main_menu(message.chat.id)

def main_menu(chat_id, msg_id=None):
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
        "✅ Pause/Resume\n\n"
        "নিচে থেকে setup করুন 👇"
    )

    if msg_id:
        bot.edit_message_text(text, chat_id, msg_id, reply_markup=markup, parse_mode='Markdown')
    else:
        bot.send_message(chat_id, text, reply_markup=markup, parse_mode='Markdown')

# ==================== SOURCE CHANNEL ====================
@bot.callback_query_handler(func=lambda c: c.data == 'set_source')
def set_source(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(
        call.message.chat.id,
        "📥 *Source Channel ID* পাঠান:\n\n"
        "Example: `-1001234567890`\n\n"
        "💡 ID পেতে @userinfobot এ channel forward করুন",
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
    main_menu(user_id)

# ==================== TARGET CHANNELS ====================
@bot.callback_query_handler(func=lambda c: c.data == 'set_targets')
def set_targets(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(
        call.message.chat.id,
        "📤 *Target Channel IDs* পাঠান (comma দিয়ে আলাদা):\n\n"
        "Example:\n`-1001111,-1002222,-1003333`",
        parse_mode='Markdown'
    )
    bot.register_next_step_handler(msg, save_targets)

def save_targets(message):
    user_id = message.from_user.id
    channels = [c.strip() for c in message.text.split(',')]
    invalid = [c for c in channels if not c.startswith('-100')]
    if invalid:
        bot.send_message(user_id, f"❌ Invalid IDs: {', '.join(invalid)}")
        return
    update_filter(user_id, target_channels=','.join(channels))
    bot.send_message(user_id, f"✅ {len(channels)}টি Target Channel set!")
    main_menu(user_id)

# ==================== WATERMARK ====================
@bot.callback_query_handler(func=lambda c: c.data == 'set_watermark')
def set_watermark(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(
        call.message.chat.id,
        "💧 *Watermark* text পাঠান:\n\n"
        "Example: `© My Channel @username`\n\n"
        "বন্ধ করতে `off` পাঠান",
        parse_mode='Markdown'
    )
    bot.register_next_step_handler(msg, save_watermark)

def save_watermark(message):
    user_id = message.from_user.id
    text = '' if message.text.strip().lower() == 'off' else message.text.strip()
    update_filter(user_id, watermark=text)
    status = "বন্ধ করা হয়েছে ✅" if not text else f"set: `{text}` ✅"
    bot.send_message(user_id, f"💧 Watermark {status}", parse_mode='Markdown')
    main_menu(user_id)

# ==================== TEXT REPLACE ====================
@bot.callback_query_handler(func=lambda c: c.data == 'set_replace')
def set_replace(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(
        call.message.chat.id,
        "🔤 *Text Replace* সেট করুন:\n\n"
        "Format: `পুরনো_text|নতুন_text`\n"
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
    main_menu(user_id)

# ==================== FILTER MENU ====================
@bot.callback_query_handler(func=lambda c: c.data == 'filter_menu')
def filter_menu(call):
    user_id = call.message.chat.id
    f = get_filter(user_id)

    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton(
            f"📝 Text {'✅' if f[6] else '❌'}", callback_data='toggle_text'),
        types.InlineKeyboardButton(
            f"🖼️ Photo {'✅' if f[7] else '❌'}", callback_data='toggle_photo')
    )
    markup.row(
        types.InlineKeyboardButton(
            f"🎥 Video {'✅' if f[8] else '❌'}", callback_data='toggle_video'),
        types.InlineKeyboardButton(
            f"📄 Document {'✅' if f[9] else '❌'}", callback_data='toggle_document')
    )
    markup.row(types.InlineKeyboardButton('⬅️ Back', callback_data='go_home'))

    bot.edit_message_text(
        "🎛️ *Media Filters*\n\nকোন ধরনের content forward হবে:",
        call.message.chat.id, call.message.message_id,
        reply_markup=markup, parse_mode='Markdown'
    )

@bot.callback_query_handler(func=lambda c: c.data.startswith('toggle_'))
def toggle_filter(call):
    user_id = call.message.chat.id
    field_map = {
        'toggle_text': 'allow_text',
        'toggle_photo': 'allow_photo',
        'toggle_video': 'allow_video',
        'toggle_document': 'allow_document'
    }
    field = field_map[call.data]
    f = get_filter(user_id)
    col_map = {
        'allow_text': 6, 'allow_photo': 7,
        'allow_video': 8, 'allow_document': 9
    }
    current = f[col_map[field]]
    update_filter(user_id, **{field: 0 if current else 1})
    filter_menu(call)

# ==================== SETTINGS MENU ====================
@bot.callback_query_handler(func=lambda c: c.data == 'settings_menu')
def settings_menu(call):
    user_id = call.message.chat.id
    f = get_filter(user_id)

    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton(
            f"🔇 Silent {'✅' if f[3] else '❌'}", callback_data='toggle_silent'),
        types.InlineKeyboardButton(
            f"{'▶️ Resume' if f[4] else '⏸️ Pause'}", callback_data='toggle_pause')
    )
    markup.row(
        types.InlineKeyboardButton('🗑️ Auto Delete', callback_data='set_autodelete')
    )
    markup.row(types.InlineKeyboardButton('⬅️ Back', callback_data='go_home'))

    bot.edit_message_text(
        "⚙️ *Settings*\n\n"
        f"🔇 Silent Mode: {'চালু' if f[3] else 'বন্ধ'}\n"
        f"⏸️ Forwarding: {'বন্ধ (Paused)' if f[4] else 'চালু (Active)'}\n"
        f"🗑️ Auto Delete: {f[11]} mins (0 = off)",
        call.message.chat.id, call.message.message_id,
        reply_markup=markup, parse_mode='Markdown'
    )

@bot.callback_query_handler(func=lambda c: c.data == 'toggle_silent')
def toggle_silent(call):
    user_id = call.message.chat.id
    f = get_filter(user_id)
    update_filter(user_id, silent_mode=0 if f[3] else 1)
    settings_menu(call)

@bot.callback_query_handler(func=lambda c: c.data == 'toggle_pause')
def toggle_pause(call):
    user_id = call.message.chat.id
    f = get_filter(user_id)
    update_filter(user_id, paused=0 if f[4] else 1)
    status = "⏸️ Forwarding বন্ধ!" if not f[4] else "▶️ Forwarding চালু!"
    bot.answer_callback_query(call.id, status)
    settings_menu(call)

@bot.callback_query_handler(func=lambda c: c.data == 'set_autodelete')
def set_autodelete(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(
        call.message.chat.id,
        "🗑️ কত মিনিট পর message delete হবে?\n\n"
        "Example: `10` (10 মিনিট)\n"
        "বন্ধ করতে `0` পাঠান",
        parse_mode='Markdown'
    )
    bot.register_next_step_handler(msg, save_autodelete)

def save_autodelete(message):
    user_id = message.from_user.id
    try:
        mins = int(message.text.strip())
        update_filter(user_id, auto_delete_mins=mins)
        status = "বন্ধ" if mins == 0 else f"{mins} মিনিট"
        bot.send_message(user_id, f"✅ Auto Delete: {status}")
    except:
        bot.send_message(user_id, "❌ সংখ্যা দিন!")
    main_menu(user_id)

# ==================== STATS ====================
@bot.callback_query_handler(func=lambda c: c.data == 'show_stats')
def show_stats(call):
    user_id = call.message.chat.id
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute('SELECT * FROM stats WHERE user_id = ?', (user_id,))
    row = c.fetchone()
    conn.close()

    fwd = row[1] if row else 0
    edt = row[2] if row else 0
    dlt = row[3] if row else 0

    markup = types.InlineKeyboardMarkup()
    markup.row(types.InlineKeyboardButton('⬅️ Back', callback_data='go_home'))

    bot.edit_message_text(
        f"📊 *আপনার Statistics*\n\n"
        f"📨 Total Forwarded: *{fwd}*\n"
        f"✏️ Total Edited: *{edt}*\n"
        f"🗑️ Total Deleted: *{dlt}*",
        call.message.chat.id, call.message.message_id,
        reply_markup=markup, parse_mode='Markdown'
    )

# ==================== HELP ====================
@bot.callback_query_handler(func=lambda c: c.data == 'show_help')
def show_help(call):
    markup = types.InlineKeyboardMarkup()
    markup.row(types.InlineKeyboardButton('⬅️ Back', callback_data='go_home'))

    bot.edit_message_text(
        "❓ *How to Use*\n\n"
        "1️⃣ Bot কে Source ও Target channel এ *Admin* বানান\n"
        "2️⃣ Source Channel ID set করুন\n"
        "3️⃣ Target Channel IDs set করুন\n"
        "4️⃣ Watermark ও filters set করুন\n"
        "5️⃣ Source এ post করুন — auto forward! ✅\n\n"
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
    main_menu(call.message.chat.id, call.message.message_id)

# ==================== FORWARD ENGINE ====================
@bot.channel_post_handler(func=lambda msg: True)
def handle_post(msg):
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute(
        'SELECT user_id, target_channels, watermark, silent_mode, '
        'allow_text, allow_photo, allow_video, allow_document, '
        'text_replace, auto_delete_mins FROM filters '
        'WHERE source_channel = ? AND paused = 0',
        (str(msg.chat.id),)
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

        for ch in targets:
            try:
                sent = None
                kwargs = {'disable_notification': bool(silent)}

                def apply_replace(text):
                    if text_replace and '|' in text_replace:
                        old, new = text_replace.split('|', 1)
                        return text.replace(old, new)
                    return text

                if msg.text and a_text:
                    sent = bot.send_message(int(ch), apply_replace(msg.text) + wm, **kwargs)
                elif msg.photo and a_photo:
                    cap = apply_replace(msg.caption or '') + wm
                    sent = bot.send_photo(int(ch), msg.photo[-1].file_id, caption=cap, **kwargs)
                elif msg.video and a_video:
                    cap = apply_replace(msg.caption or '') + wm
                    sent = bot.send_video(int(ch), msg.video.file_id, caption=cap, **kwargs)
                elif msg.document and a_doc:
                    cap = apply_replace(msg.caption or '') + wm
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

                    # Auto delete
                    if auto_del and auto_del > 0:
                        import threading
                        def delete_later(cid, mid, delay):
                            time.sleep(delay * 60)
                            try:
                                bot.delete_message(int(cid), mid)
                                update_stats(user_id, 'total_deleted')
                            except:
                                pass
                        threading.Thread(
                            target=delete_later,
                            args=(ch, sent.message_id, auto_del),
                            daemon=True
                        ).start()

            except Exception as e:
                print(f"Forward error: {e}")

# ==================== EDIT ENGINE ====================
@bot.edited_channel_post_handler(func=lambda msg: True)
def handle_edit(msg):
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute(
        'SELECT mm.channel_id, mm.sent_msg_id, f.watermark, f.text_replace '
        'FROM msg_map mm JOIN filters f ON mm.user_id = f.user_id '
        'WHERE mm.source_msg_id = ? AND f.source_channel = ?',
        (msg.message_id, str(msg.chat.id))
    )
    rows = c.fetchall()
    conn.close()

    for row in rows:
        channel_id, sent_msg_id, watermark, text_replace = row
        wm = f"\n\n{watermark}" if watermark else ''

        def apply_replace(text):
            if text_replace and '|' in text_replace:
                old, new = text_replace.split('|', 1)
                return text.replace(old, new)
            return text

        try:
            if msg.text:
                bot.edit_message_text(
                    apply_replace(msg.text) + wm, int(channel_id), sent_msg_id)
            elif msg.caption is not None:
                bot.edit_message_caption(
                    apply_replace(msg.caption) + wm, int(channel_id), sent_msg_id)
            update_stats(row[0], 'total_edited')
        except Exception as e:
            print(f"Edit error: {e}")

# ==================== ADMIN ====================
@bot.message_handler(commands=['admin'])
def admin(message):
    if message.from_user.id != ADMIN_ID:
        return
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM users')
    total = c.fetchone()[0]
    c.execute('SELECT COUNT(*) FROM filters WHERE paused = 0 AND source_channel IS NOT NULL')
    active = c.fetchone()[0]
    conn.close()

    markup = types.InlineKeyboardMarkup()
    markup.row(types.InlineKeyboardButton('📢 Broadcast', callback_data='admin_broadcast'))

    bot.send_message(
        ADMIN_ID,
        f"🔐 *Admin Panel*\n\n"
        f"👥 Total Users: *{total}*\n"
        f"✅ Active Bots: *{active}*",
        reply_markup=markup, parse_mode='Markdown'
    )

@bot.callback_query_handler(func=lambda c: c.data == 'admin_broadcast')
def admin_broadcast(call):
    if call.from_user.id != ADMIN_ID:
        return
    msg = bot.send_message(ADMIN_ID, "📢 Broadcast message পাঠান:")
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
        except:
            pass

    bot.send_message(ADMIN_ID, f"✅ {success}/{len(users)} জনকে পাঠানো হয়েছে!")

# ==================== RUN ====================
print("✅ ForwardPro Bot running...")
bot.infinity_polling(timeout=60, long_polling_timeout=60)
