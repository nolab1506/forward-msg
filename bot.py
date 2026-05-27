import os
import telebot

BOT_TOKEN = os.environ.get('BOT_TOKEN')
bot = telebot.TeleBot(BOT_TOKEN)

SOURCE_CHANNEL = int(os.environ.get('SOURCE_CHANNEL'))
TARGET_CHANNELS = [int(x) for x in os.environ.get('TARGET_CHANNELS', '').split(',')]
WATERMARK = os.environ.get('WATERMARK', '\n\n© My Channel')

msg_map = {}

@bot.message_handler(commands=['start'])
def start(message):
    bot.send_message(message.chat.id, "✅ ForwardPro Bot চালু আছে!")

@bot.channel_post_handler(func=lambda msg: msg.chat.id == SOURCE_CHANNEL)
def forward(msg):
    msg_map[msg.message_id] = {}
    for ch in TARGET_CHANNELS:
        try:
            if msg.text:
                sent = bot.send_message(ch, msg.text + WATERMARK)
            elif msg.photo:
                sent = bot.send_photo(ch, msg.photo[-1].file_id, caption=(msg.caption or '') + WATERMARK)
            elif msg.video:
                sent = bot.send_video(ch, msg.video.file_id, caption=(msg.caption or '') + WATERMARK)
            elif msg.document:
                sent = bot.send_document(ch, msg.document.file_id, caption=(msg.caption or '') + WATERMARK)
            else:
                continue
            msg_map[msg.message_id][ch] = sent.message_id
        except Exception as e:
            print(f"Error: {e}")

@bot.edited_channel_post_handler(func=lambda msg: msg.chat.id == SOURCE_CHANNEL)
def edit(msg):
    if msg.message_id not in msg_map:
        return
    for ch, sent_id in msg_map[msg.message_id].items():
        try:
            if msg.text:
                bot.edit_message_text(msg.text + WATERMARK, ch, sent_id)
            elif msg.caption is not None:
                bot.edit_message_caption(msg.caption + WATERMARK, ch, sent_id)
        except Exception as e:
            print(f"Edit error: {e}")

print("Bot running...")
bot.infinity_polling(timeout=60, long_polling_timeout=60)
