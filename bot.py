import os
import telebot
from telebot import types

# ==================== Config ====================
BOT_TOKEN = os.environ.get('BOT_TOKEN')
bot = telebot.TeleBot(BOT_TOKEN)

# Source channel যেখান থেকে forward হবে
SOURCE_CHANNEL = int(os.environ.get('SOURCE_CHANNEL'))

# Target channels যেখানে forward হবে
TARGET_CHANNELS = [
    int(x) for x in os.environ.get('TARGET_CHANNELS', '').split(',')
]

# Watermark text
WATERMARK = os.environ.get('WATERMARK', '\n\n© My Channel')

# Message ID mapping: {source_msg_id: {channel_id: sent_msg_id}}
msg_map = {}

# ==================== New Message ====================
@bot.channel_post_handler(func=lambda msg: msg.chat.id == SOURCE_CHANNEL)
def forward_message(msg):
    msg_map[msg.message_id] = {}

    for channel in TARGET_CHANNELS:
        try:
            # Text message
            if msg.text:
                sent = bot.send_message(channel, msg.text + WATERMARK)
                msg_map[msg.message_id][channel] = sent.message_id

            # Photo
            elif msg.photo:
                caption = (msg.caption or '') + WATERMARK
                sent = bot.send_photo(
                    channel,
                    msg.photo[-1].file_id,
                    caption=caption
                )
                msg_map[msg.message_id][channel] = sent.message_id

            # Video
            elif msg.video:
                caption = (msg.caption or '') + WATERMARK
                sent = bot.send_video(
                    channel,
                    msg.video.file_id,
                    caption=caption
                )
                msg_map[msg.message_id][channel] = sent.message_id

            # Document
            elif msg.document:
                caption = (msg.caption or '') + WATERMARK
                sent = bot.send_document(
                    channel,
                    msg.document.file_id,
                    caption=caption
                )
                msg_map[msg.message_id][channel] = sent.message_id

        except Exception as e:
            print(f"Error sending to {channel}: {e}")

# ==================== Edit Message ====================
@bot.edited_channel_post_handler(func=lambda msg: msg.chat.id == SOURCE_CHANNEL)
def edit_message(msg):
    if msg.message_id not in msg_map:
        return

    for channel, sent_id in msg_map[msg.message_id].items():
        try:
            # Text edit
            if msg.text:
                bot.edit_message_text(
                    msg.text + WATERMARK,
                    channel,
                    sent_id
                )

            # Photo/Video/Document caption edit
            elif msg.photo or msg.video or msg.document:
                caption = (msg.caption or '') + WATERMARK
                bot.edit_message_caption(
                    caption,
                    channel,
                    sent_id
                )

        except Exception as e:
            print(f"Error editing in {channel}: {e}")

print("Forward Bot running...")
bot.infinity_polling(timeout=60, long_polling_timeout=60)
