import os
import sqlite3
import time
import requests
import telebot
from telebot import types
from datetime import datetime, timedelta

# ==================== CONFIG ====================
BOT_TOKEN = os.environ.get('BOT_TOKEN')
NOWPAYMENTS_API_KEY = os.environ.get('NOWPAYMENTS_API_KEY')
ADMIN_ID = int(os.environ.get('ADMIN_ID'))

bot = telebot.TeleBot(BOT_TOKEN)

# ==================== PLANS ====================
PLANS = {
    'basic': {
        'name': '⭐ Basic Plan',
        'price': 2,
        'duration_days': 30,
        'max_targets': 5,
        'watermark': True,
        'description': '5টি চ্যানেলে ফরওয়ার্ড + Watermark'
    },
    'pro': {
        'name': '🚀 Pro Plan',
        'price': 4,
        'duration_days': 30,
        'max_targets': 10,
        'watermark': True,
        'description': '10টি চ্যানেলে ফরওয়ার্ড + Watermark'
    },
    'premium': {
        'name': '💎 Premium Plan',
        'price': 7,
        'duration_days': 30,
        'max_targets': 999,
        'watermark': True,
        'description': 'Unlimited চ্যানেল + Custom Watermark'
    }
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
        source_channel TEXT DEFAULT NULL,
        target_channels TEXT DEFAULT NULL,
        watermark TEXT DEFAULT '© My Channel',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS payments (
        payment_id TEXT PRIMARY KEY,
        user_id INTEGER,
        plan TEXT,
        amount REAL,
        status TEXT DEFAULT 'pending',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS msg_map (
        source_msg_id INTEGER,
        user_id INTEGER,
        channel_id TEXT,
        sent_msg_id INTEGER,
        PRIMARY KEY (source_msg_id, user_id, channel_id)
    )''')
    
    conn.commit()
    conn.close()

init_db()

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
    c.execute('''INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)''',
              (user_id, username))
    conn.commit()
    conn.close()

def update_user(user_id, **kwargs):
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    for key, val in kwargs.items():
        c.execute(f'UPDATE users SET {key} = ? WHERE user_id = ?', (val, user_id))
    conn.commit()
    conn.close()

def is_subscribed(user_id):
    user = get_user(user_id)
    if not user or not user[3]:
        return False
    expires = datetime.fromisoformat(user[3])
    return expires > datetime.now()

def get_plan(user_id):
    user = get_user(user_id)
    if not user:
        return None
    return user[2]

# ==================== START ====================
@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.from_user.id
    username = message.from_user.username or 'Unknown'
    save_user(user_id, username)

    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton('📦 Plans & Pricing', callback_data='show_plans'),
        types.InlineKeyboardButton('⚙️ My Account', callback_data='my_account')
    )
    markup.row(
        types.InlineKeyboardButton('📖 How to Use', callback_data='how_to_use'),
        types.InlineKeyboardButton('💬 Support', url='https://t.me/yoursupport')
    )

    bot.send_message(
        user_id,
        "╔══════════════════════╗\n"
        "║   📡 *ForwardPro Bot*   ║\n"
        "╚══════════════════════╝\n\n"
        "একটি চ্যানেলে পোস্ট করুন,\n"
        "বাকি সব চ্যানেলে *অটো ফরওয়ার্ড* হবে! ⚡\n\n"
        "✅ Auto Forward\n"
        "✅ Auto Edit Sync\n"
        "✅ Custom Watermark\n"
        "✅ Unlimited Channels (Premium)\n\n"
        "নিচে থেকে শুরু করুন 👇",
        reply_markup=markup,
        parse_mode='Markdown'
    )

# ==================== PLANS ====================
@bot.callback_query_handler(func=lambda c: c.data == 'show_plans')
def show_plans(call):
    markup = types.InlineKeyboardMarkup()
    for key, plan in PLANS.items():
        markup.row(types.InlineKeyboardButton(
            f"{plan['name']} — ${plan['price']}/month",
            callback_data=f'buy_{key}'
        ))
    markup.row(types.InlineKeyboardButton('⬅️ Back', callback_data='go_home'))

    bot.edit_message_text(
        "💰 *Available Plans*\n\n"
        "⭐ *Basic — $2/month*\n"
        "└ 5 চ্যানেলে ফরওয়ার্ড + Watermark\n\n"
        "🚀 *Pro — $4/month*\n"
        "└ 10 চ্যানেলে ফরওয়ার্ড + Watermark\n\n"
        "💎 *Premium — $7/month*\n"
        "└ Unlimited চ্যানেল + Custom Watermark\n\n"
        "একটি plan সিলেক্ট করুন 👇",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup,
        parse_mode='Markdown'
    )

# ==================== BUY PLAN ====================
@bot.callback_query_handler(func=lambda c: c.data.startswith('buy_'))
def buy_plan(call):
    user_id = call.message.chat.id
    plan_key = call.data.split('_')[1]
    plan = PLANS[plan_key]

    try:
        # NOWPayments API দিয়ে payment তৈরি করা
        response = requests.post(
            'https://api.nowpayments.io/v1/payment',
            headers={
                'x-api-key': NOWPAYMENTS_API_KEY,
                'Content-Type': 'application/json'
            },
            json={
                'price_amount': plan['price'],
                'price_currency': 'usd',
                'pay_currency': 'usdttrc20',
                'order_id': f'{user_id}_{plan_key}_{int(time.time())}',
                'order_description': f'ForwardPro {plan["name"]}'
            }
        )
        data = response.json()

        if 'pay_address' in data:
            payment_id = data['payment_id']
            pay_address = data['pay_address']
            pay_amount = data['pay_amount']

            # DB তে সেভ করো
            conn = sqlite3.connect('bot.db')
            c = conn.cursor()
            c.execute(
                'INSERT OR REPLACE INTO payments VALUES (?, ?, ?, ?, ?, ?)',
                (str(payment_id), user_id, plan_key, plan['price'], 'pending',
                 datetime.now().isoformat())
            )
            conn.commit()
            conn.close()

            markup = types.InlineKeyboardMarkup()
            markup.row(types.InlineKeyboardButton(
                '✅ Payment করেছি, Verify করো',
                callback_data=f'verify_{payment_id}'
            ))
            markup.row(types.InlineKeyboardButton('⬅️ Back', callback_data='show_plans'))

            bot.edit_message_text(
                f"💳 *Payment Details*\n\n"
                f"Plan: *{plan['name']}*\n"
                f"Amount: *{pay_amount} USDT (TRC20)*\n\n"
                f"📋 *Wallet Address:*\n"
                f"`{pay_address}`\n\n"
                f"⚠️ শুধুমাত্র *TRC20 USDT* পাঠান\n"
                f"Payment করার পর নিচের বাটন চাপুন 👇",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=markup,
                parse_mode='Markdown'
            )
        else:
            bot.answer_callback_query(call.id, "Payment তৈরিতে সমস্যা হয়েছে!")

    except Exception as e:
        bot.answer_callback_query(call.id, f"Error: {str(e)}")

# ==================== VERIFY PAYMENT ====================
@bot.callback_query_handler(func=lambda c: c.data.startswith('verify_'))
def verify_payment(call):
    user_id = call.message.chat.id
    payment_id = call.data.split('_')[1]

    bot.answer_callback_query(call.id, "Payment চেক করা হচ্ছে...")

    try:
        response = requests.get(
            f'https://api.nowpayments.io/v1/payment/{payment_id}',
            headers={'x-api-key': NOWPAYMENTS_API_KEY}
        )
        data = response.json()
        status = data.get('payment_status', '')

        if status in ['finished', 'confirmed', 'partially_paid']:
            conn = sqlite3.connect('bot.db')
            c = conn.cursor()
            c.execute('SELECT plan FROM payments WHERE payment_id = ?', (str(payment_id),))
            row = c.fetchone()

            if row:
                plan_key = row[0]
                expires = (datetime.now() + timedelta(days=30)).isoformat()
                c.execute(
                    'UPDATE users SET plan = ?, expires_at = ? WHERE user_id = ?',
                    (plan_key, expires, user_id)
                )
                c.execute(
                    'UPDATE payments SET status = ? WHERE payment_id = ?',
                    ('completed', str(payment_id))
                )
                conn.commit()
                conn.close()

                markup = types.InlineKeyboardMarkup()
                markup.row(types.InlineKeyboardButton(
                    '⚙️ Setup করুন', callback_data='setup_bot'
                ))

                bot.edit_message_text(
                    f"✅ *Payment Successful!*\n\n"
                    f"Plan: *{PLANS[plan_key]['name']}*\n"
                    f"Expires: *{expires[:10]}*\n\n"
                    f"এখন আপনার bot setup করুন 👇",
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=markup,
                    parse_mode='Markdown'
                )

                # Admin কে notify করো
                bot.send_message(
                    ADMIN_ID,
                    f"💰 *New Payment!*\n"
                    f"User: `{user_id}`\n"
                    f"Plan: {PLANS[plan_key]['name']}\n"
                    f"Amount: ${PLANS[plan_key]['price']}",
                    parse_mode='Markdown'
                )
            else:
                conn.close()
                bot.answer_callback_query(call.id, "Payment record পাওয়া যায়নি!")
        else:
            bot.send_message(
                user_id,
                "⏳ Payment এখনো confirm হয়নি।\n"
                "কিছুক্ষণ পর আবার try করুন।"
            )
    except Exception as e:
        bot.answer_callback_query(call.id, f"Error: {str(e)}")

# ==================== SETUP ====================
@bot.callback_query_handler(func=lambda c: c.data == 'setup_bot')
def setup_bot(call):
    user_id = call.message.chat.id

    if not is_subscribed(user_id):
        bot.answer_callback_query(call.id, "আপনার কোনো active plan নেই!")
        return

    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton('📥 Source Channel Set', callback_data='set_source'),
        types.InlineKeyboardButton('📤 Target Channels Set', callback_data='set_targets')
    )
    markup.row(
        types.InlineKeyboardButton('💧 Watermark Set', callback_data='set_watermark'),
        types.InlineKeyboardButton('▶️ Start Bot', callback_data='activate_forward')
    )
    markup.row(types.InlineKeyboardButton('⬅️ Back', callback_data='my_account'))

    bot.edit_message_text(
        "⚙️ *Bot Setup*\n\n"
        "নিচের সব কিছু সেট করুন:\n\n"
        "1️⃣ Source Channel — যেখান থেকে forward হবে\n"
        "2️⃣ Target Channels — যেখানে forward হবে\n"
        "3️⃣ Watermark — আপনার custom text\n"
        "4️⃣ Start Bot — সব কিছু activate করুন",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup,
        parse_mode='Markdown'
    )

# ==================== SET SOURCE CHANNEL ====================
@bot.callback_query_handler(func=lambda c: c.data == 'set_source')
def set_source(call):
    user_id = call.message.chat.id
    bot.send_message(
        user_id,
        "📥 আপনার *Source Channel* এর ID পাঠান:\n\n"
        "Example: `-1001234567890`\n\n"
        "💡 Channel ID পেতে @userinfobot এ channel forward করুন",
        parse_mode='Markdown'
    )
    bot.register_next_step_handler(call.message, save_source)

def save_source(message):
    user_id = message.from_user.id
    channel_id = message.text.strip()

    if not channel_id.startswith('-100'):
        bot.send_message(user_id, "❌ Invalid ID! `-100` দিয়ে শুরু হওয়া ID দিন।", parse_mode='Markdown')
        return

    update_user(user_id, source_channel=channel_id)
    bot.send_message(user_id, f"✅ Source Channel set: `{channel_id}`", parse_mode='Markdown')

# ==================== SET TARGET CHANNELS ====================
@bot.callback_query_handler(func=lambda c: c.data == 'set_targets')
def set_targets(call):
    user_id = call.message.chat.id
    plan_key = get_plan(user_id)
    max_t = PLANS[plan_key]['max_targets'] if plan_key else 5

    bot.send_message(
        user_id,
        f"📤 Target Channel IDs পাঠান (comma দিয়ে আলাদা করুন):\n\n"
        f"আপনার plan এ সর্বোচ্চ *{max_t}টি* channel\n\n"
        f"Example:\n`-1001111,-1002222,-1003333`",
        parse_mode='Markdown'
    )
    bot.register_next_step_handler(call.message, save_targets)

def save_targets(message):
    user_id = message.from_user.id
    plan_key = get_plan(user_id)
    max_t = PLANS[plan_key]['max_targets'] if plan_key else 5

    channels = [c.strip() for c in message.text.split(',')]

    if len(channels) > max_t:
        bot.send_message(
            user_id,
            f"❌ আপনার plan এ সর্বোচ্চ {max_t}টি channel!\n"
            f"Upgrade করতে /start দিন।"
        )
        return

    invalid = [c for c in channels if not c.startswith('-100')]
    if invalid:
        bot.send_message(user_id, f"❌ Invalid IDs: {', '.join(invalid)}", parse_mode='Markdown')
        return

    update_user(user_id, target_channels=','.join(channels))
    bot.send_message(user_id, f"✅ {len(channels)}টি Target Channel set হয়েছে!")

# ==================== SET WATERMARK ====================
@bot.callback_query_handler(func=lambda c: c.data == 'set_watermark')
def set_watermark(call):
    user_id = call.message.chat.id
    bot.send_message(
        user_id,
        "💧 আপনার Watermark text পাঠান:\n\n"
        "Example: `© My Awesome Channel`\n\n"
        "এটি প্রতিটি message এর নিচে যুক্ত হবে।",
        parse_mode='Markdown'
    )
    bot.register_next_step_handler(call.message, save_watermark)

def save_watermark(message):
    user_id = message.from_user.id
    watermark = message.text.strip()
    update_user(user_id, watermark=watermark)
    bot.send_message(user_id, f"✅ Watermark set: `{watermark}`", parse_mode='Markdown')

# ==================== MY ACCOUNT ====================
@bot.callback_query_handler(func=lambda c: c.data == 'my_account')
def my_account(call):
    user_id = call.message.chat.id
    user = get_user(user_id)

    if not user:
        bot.answer_callback_query(call.id, "Account পাওয়া যায়নি!")
        return

    plan = user[2] or 'None'
    expires = user[3][:10] if user[3] else 'N/A'
    source = user[4] or 'Not set'
    targets = len(user[5].split(',')) if user[5] else 0
    watermark = user[6] or 'Not set'
    status = "✅ Active" if is_subscribed(user_id) else "❌ Expired"

    markup = types.InlineKeyboardMarkup()
    markup.row(types.InlineKeyboardButton('⚙️ Setup Bot', callback_data='setup_bot'))
    markup.row(types.InlineKeyboardButton('💰 Upgrade Plan', callback_data='show_plans'))
    markup.row(types.InlineKeyboardButton('⬅️ Back', callback_data='go_home'))

    bot.edit_message_text(
        f"👤 *My Account*\n\n"
        f"🆔 ID: `{user_id}`\n"
        f"📦 Plan: *{plan.capitalize() if plan != 'None' else 'No Plan'}*\n"
        f"📅 Expires: *{expires}*\n"
        f"🔰 Status: {status}\n\n"
        f"📥 Source: `{source}`\n"
        f"📤 Targets: *{targets} channels*\n"
        f"💧 Watermark: `{watermark}`",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup,
        parse_mode='Markdown'
    )

# ==================== FORWARD ENGINE ====================
@bot.channel_post_handler(func=lambda msg: True)
def handle_channel_post(msg):
    # সব active user এর মধ্যে এই source channel কে match করো
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute(
        'SELECT user_id, target_channels, watermark FROM users '
        'WHERE source_channel = ? AND plan IS NOT NULL AND expires_at > ?',
        (str(msg.chat.id), datetime.now().isoformat())
    )
    users = c.fetchall()
    conn.close()

    for user in users:
        user_id, target_channels_str, watermark = user
        if not target_channels_str:
            continue

        targets = target_channels_str.split(',')

        for channel in targets:
            try:
                sent = None
                wm = f"\n\n{watermark}" if watermark else ''

                if msg.text:
                    sent = bot.send_message(int(channel), msg.text + wm)
                elif msg.photo:
                    sent = bot.send_photo(
                        int(channel), msg.photo[-1].file_id,
                        caption=(msg.caption or '') + wm
                    )
                elif msg.video:
                    sent = bot.send_video(
                        int(channel), msg.video.file_id,
                        caption=(msg.caption or '') + wm
                    )
                elif msg.document:
                    sent = bot.send_document(
                        int(channel), msg.document.file_id,
                        caption=(msg.caption or '') + wm
                    )

                if sent:
                    db = sqlite3.connect('bot.db')
                    db.execute(
                        'INSERT OR REPLACE INTO msg_map VALUES (?, ?, ?, ?)',
                        (msg.message_id, user_id, channel, sent.message_id)
                    )
                    db.commit()
                    db.close()

            except Exception as e:
                print(f"Forward error to {channel}: {e}")

# ==================== EDIT ENGINE ====================
@bot.edited_channel_post_handler(func=lambda msg: True)
def handle_edit(msg):
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute(
        'SELECT mm.channel_id, mm.sent_msg_id, u.watermark '
        'FROM msg_map mm JOIN users u ON mm.user_id = u.user_id '
        'WHERE mm.source_msg_id = ? AND mm.user_id IN ('
        'SELECT user_id FROM users WHERE source_channel = ?)',
        (msg.message_id, str(msg.chat.id))
    )
    rows = c.fetchall()
    conn.close()

    for row in rows:
        channel_id, sent_msg_id, watermark = row
        wm = f"\n\n{watermark}" if watermark else ''

        try:
            if msg.text:
                bot.edit_message_text(msg.text + wm, int(channel_id), sent_msg_id)
            elif msg.caption is not None:
                bot.edit_message_caption(msg.caption + wm, int(channel_id), sent_msg_id)
        except Exception as e:
            print(f"Edit error in {channel_id}: {e}")

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
    c.execute("SELECT SUM(amount) FROM payments WHERE status = 'completed'")
    revenue = c.fetchone()[0] or 0
    conn.close()

    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton('👥 All Users', callback_data='admin_users'),
        types.InlineKeyboardButton('📢 Broadcast', callback_data='admin_broadcast')
    )

    bot.send_message(
        ADMIN_ID,
        f"🔐 *Admin Panel*\n\n"
        f"👥 Total Users: *{total}*\n"
        f"✅ Active Subscribers: *{active}*\n"
        f"💰 Total Revenue: *${revenue:.2f}*",
        reply_markup=markup,
        parse_mode='Markdown'
    )

# ==================== HOW TO USE ====================
@bot.callback_query_handler(func=lambda c: c.data == 'how_to_use')
def how_to_use(call):
    markup = types.InlineKeyboardMarkup()
    markup.row(types.InlineKeyboardButton('⬅️ Back', callback_data='go_home'))

    bot.edit_message_text(
        "📖 *How to Use ForwardPro*\n\n"
        "1️⃣ একটি Plan কিনুন\n"
        "2️⃣ Bot কে সব channel এ *Admin* বানান\n"
        "3️⃣ Source ও Target channel set করুন\n"
        "4️⃣ Watermark set করুন\n"
        "5️⃣ Source channel এ post করুন\n"
        "6️⃣ সব channel এ auto forward হবে! ✅\n\n"
        "⚠️ *Channel ID পাওয়ার উপায়:*\n"
        "@userinfobot এ channel forward করুন",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup,
        parse_mode='Markdown'
    )

# ==================== GO HOME ====================
@bot.callback_query_handler(func=lambda c: c.data == 'go_home')
def go_home(call):
    bot.delete_message(call.message.chat.id, call.message.message_id)
    start(call.message)

# ==================== RUN ====================
print("✅ ForwardPro Bot is running...")
bot.infinity_polling(timeout=60, long_polling_timeout=60)
