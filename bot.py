# -*- coding: utf-8 -*-
import sys
import time
import threading
import asyncio
import os
import re
import traceback
import logging
from collections import defaultdict
from pathlib import Path

import requests
from PIL import Image
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from telegram.constants import ParseMode

# ====== دریافت متغیرهای محیطی ======
TELEGRAM_TOKEN = "8286435359:AAHUBJ-_WvQCz4pHkF-WqT8ypuk7lYCNnZI"
GROQ_API_KEY = "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"
OCR_API_KEY = "K86067744288957"

if not TELEGRAM_TOKEN or not GROQ_API_KEY:
    raise ValueError("لطفاً متغیرهای محیطی TELEGRAM_TOKEN و GROQ_API_KEY را تنظیم کنید.")

# ====== تنظیمات ======
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = "llama-3.3-70b-versatile"
IMAGE_GEN_API = "https://image.pollinations.ai/prompt"
OCR_URL = "https://api.ocr.space/parse/image"
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "https://mbzmyai.onrender.com/webhook")
TIMEOUT = 60
DOWNLOAD_PATH = "/tmp/groqbot_files"

Path(DOWNLOAD_PATH).mkdir(parents=True, exist_ok=True)

app = Flask(__name__)

# تاریخچه مکالمات
user_history = defaultdict(list)

# تنظیمات لاگ
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ====== تشخیص زبان ساده ======
def detect_language(text):
    persian_chars = set("ابپتثجچحخدذرزژسشصضطظعغفقکگلمنوهیئءآاًهٔ")
    return "persian" if any(ch in persian_chars for ch in text) else "english"

# ====== درخواست به Groq ======
def ask_groq(user_message, history):
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    lang = detect_language(user_message)
    system = "شما یک دستیار فارسی‌دان هستید. به فارسی پاسخ دهید." if lang == "persian" else "You are a helpful assistant. Answer in the same language as the user."
    messages = [{"role": "system", "content": system}] + history[-5:] + [{"role": "user", "content": user_message}]
    payload = {"model": MODEL, "messages": messages, "temperature": 0.7, "max_tokens": 2000}
    try:
        r = requests.post(GROQ_URL, headers=headers, json=payload, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    except requests.exceptions.Timeout:
        return "⏱️ زمان درخواست تمام شد. اینترنت خود را بررسی کنید."
    except requests.exceptions.ConnectionError:
        return "🔌 خطای اتصال به اینترنت."
    except requests.exceptions.HTTPError as e:
        if r.status_code == 401:
            return "❌ کلید Groq نامعتبر است."
        return f"❌ خطای HTTP: {r.status_code}"
    except Exception as e:
        return f"❌ خطای غیرمنتظره: {type(e).__name__}"

# ====== ساخت عکس با هوش مصنوعی ======
def generate_image(prompt: str) -> str:
    try:
        clean_prompt = re.sub(r'[^\w\s\u0600-\u06FF-]', '', prompt)[:200]
        url = f"{IMAGE_GEN_API}/{clean_prompt}"
        response = requests.get(url, timeout=TIMEOUT)
        if response.status_code == 200:
            filename = f"{DOWNLOAD_PATH}/gen_{int(time.time())}.jpg"
            with open(filename, 'wb') as f:
                f.write(response.content)
            return filename
        else:
            return None
    except Exception as e:
        logger.error(f"خطا در ساخت عکس: {e}")
        return None

# ====== خواندن متن عکس (OCR) ======
def extract_text_from_image(image_path: str) -> str:
    if not OCR_API_KEY:
        return "🔑 کلید OCR.space تنظیم نشده است."
    try:
        with open(image_path, 'rb') as f:
            files = {'file': f}
            data = {'apikey': OCR_API_KEY, 'language': 'per', 'isOverlayRequired': False}
            response = requests.post(OCR_URL, files=files, data=data, timeout=TIMEOUT)
            result = response.json()
            if not result.get('IsErroredOnProcessing'):
                return result['ParsedResults'][0]['ParsedText']
            else:
                return "❌ متنی در عکس یافت نشد."
    except Exception as e:
        return f"❌ خطای OCR: {type(e).__name__}"

# ====== هندلر فرمان /start با دکمه ======
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("start handler executed")
    keyboard = [[InlineKeyboardButton("🧹 پاک کردن تاریخچه", callback_data="clear")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    welcome_text = (
        "🤖 **ربات فوق‌پیشرفته Groq**\n\n"
        "✨ **قابلیت‌های ویژه:**\n"
        "• پاسخ به هر سوالی با هوش مصنوعی Groq\n"
        "• ساخت عکس با هوش مصنوعی – بگویید «عکس ... بساز»\n"
        "• خواندن متن عکس‌ها (OCR) – با ارسال عکس\n"
        "• ذخیره همه نوع فایل\n"
        "• یادگیری مکالمات قبلی شما\n"
        "• تایم‌اوت ۶۰ ثانیه – مناسب اینترنت ضعیف\n\n"
        "💬 **هر سوالی داری بپرس.**"
    )
    await update.message.reply_text(welcome_text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)

# ====== هندلر دکمه‌ها ======
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "clear":
        user_id = update.effective_user.id
        user_history[user_id].clear()
        await query.edit_message_text("🧹 تاریخچه مکالمه پاک شد.")

# ====== هندلر پیام‌های متنی ======
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("handle_text executed")
    user_id = update.effective_user.id
    text = update.message.text

    # تشخیص درخواست ساخت عکس
    if any(keyword in text for keyword in ["عکس", "تصویر", "بساز", "draw", "image", "picture", "generate"]):
        prompt = re.sub(r'(عکس|تصویر|بساز|draw|image|picture|generate|of|a|an|یک|یه)\s*', '', text, flags=re.IGNORECASE).strip()
        if not prompt:
            prompt = text
        thinking = await update.message.reply_text("🎨 در حال ساخت عکس... لطفاً صبر کنید")
        img_path = generate_image(prompt)
        if img_path:
            with open(img_path, 'rb') as f:
                await update.message.reply_photo(photo=InputFile(f), caption=f"🖼️ عکس برای: {prompt}")
            await thinking.delete()
        else:
            await thinking.edit_text("❌ ساخت عکس با خطا مواجه شد.")
        return

    # پاسخ عادی
    user_history[user_id].append({"role": "user", "content": text})
    thinking = await update.message.reply_text("⏳ در حال فکر کردن...")
    answer = ask_groq(text, user_history[user_id][:-1])
    user_history[user_id].append({"role": "assistant", "content": answer})
    await thinking.edit_text(answer)

# ====== هندلر عکس (OCR + ذخیره) ======
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("handle_photo executed")
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    timestamp = int(time.time())
    filename = f"{DOWNLOAD_PATH}/photo_{timestamp}.jpg"
    await file.download_to_drive(filename)
    await update.message.reply_text(f"🖼️ عکس ذخیره شد: {filename}")

    ocr_text = extract_text_from_image(filename)
    if ocr_text and "❌" not in ocr_text and "🔑" not in ocr_text:
        await update.message.reply_text(f"📄 **متن استخراج‌شده:**\n```\n{ocr_text}\n```", parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(ocr_text)

# ====== هندلر فیلم ======
async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("handle_video executed")
    video = update.message.video
    file = await context.bot.get_file(video.file_id)
    timestamp = int(time.time())
    filename = f"{DOWNLOAD_PATH}/video_{timestamp}.mp4"
    await file.download_to_drive(filename)
    await update.message.reply_text(f"🎬 فیلم ذخیره شد: {filename}")

# ====== هندلر صدا ======
async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("handle_voice executed")
    voice = update.message.voice
    file = await context.bot.get_file(voice.file_id)
    timestamp = int(time.time())
    filename = f"{DOWNLOAD_PATH}/voice_{timestamp}.ogg"
    await file.download_to_drive(filename)
    await update.message.reply_text(f"🎤 صدا ذخیره شد: {filename}")

# ====== هندلر سند ======
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("handle_document executed")
    doc = update.message.document
    file = await context.bot.get_file(doc.file_id)
    timestamp = int(time.time())
    ext = os.path.splitext(doc.file_name)[1] if doc.file_name else ".bin"
    filename = f"{DOWNLOAD_PATH}/doc_{timestamp}{ext}"
    await file.download_to_drive(filename)
    await update.message.reply_text(f"📄 فایل ذخیره شد: {filename}")

# ====== هندلر استیکر ======
async def handle_sticker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("handle_sticker executed")
    sticker = update.message.sticker
    if sticker.is_animated or sticker.is_video:
        await update.message.reply_text("🎭 استیکر متحرک قابل ذخیره نیست.")
    else:
        file = await context.bot.get_file(sticker.file_id)
        timestamp = int(time.time())
        filename = f"{DOWNLOAD_PATH}/sticker_{timestamp}.png"
        await file.download_to_drive(filename)
        await update.message.reply_text(f"🎭 استیکر ذخیره شد: {filename}")

# ====== هندلر سایر فایل‌ها ======
async def handle_unknown_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("handle_unknown_media executed")
    await update.message.reply_text("📦 فایل دریافت شد. قابل پردازش نیست.")

# ====== خطاهای عمومی ======
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Exception in handler: {context.error}", exc_info=context.error)
    if update and update.message:
        await update.message.reply_text("❌ خطایی رخ داد. لطفاً دوباره تلاش کنید.")

# ====== ساخت ربات ======
bot_app = Application.builder().token(TELEGRAM_TOKEN).build()
bot_app.add_handler(CommandHandler("start", start))
bot_app.add_handler(CallbackQueryHandler(button_handler))
bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
bot_app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
bot_app.add_handler(MessageHandler(filters.VIDEO, handle_video))
bot_app.add_handler(MessageHandler(filters.VOICE, handle_voice))
bot_app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
bot_app.add_handler(MessageHandler(filters.Sticker.ALL, handle_sticker))
bot_app.add_handler(MessageHandler(filters.ALL & ~filters.TEXT & ~filters.COMMAND, handle_unknown_media))
bot_app.add_error_handler(error_handler)

# ====== راه‌اندازی ربات در ترد پس‌زمینه ======
bot_ready = False
bot_loop = None

def run_bot():
    global bot_loop, bot_ready
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(bot_app.initialize())
        loop.run_until_complete(bot_app.start())
        bot_loop = loop
        bot_ready = True
        logger.info("✅ ربات آماده شد.")
        loop.run_forever()
    except Exception as e:
        logger.error(f"❌ خطا در run_bot: {e}", exc_info=True)
        bot_ready = False

threading.Thread(target=run_bot, daemon=True).start()

# ====== صبر تا آماده شدن ربات ======
for _ in range(30):
    if bot_ready:
        break
    time.sleep(1)

# ====== تنظیم Webhook ======
def set_webhook():
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook",
            data={"url": WEBHOOK_URL, "max_connections": 40}
        )
        if resp.status_code == 200 and resp.json().get("ok"):
            logger.info("✅ Webhook تنظیم شد.")
        else:
            logger.warning(f"⚠️ Webhook تنظیم نشد: {resp.text}")
    except Exception as e:
        logger.error(f"⚠️ خطا در تنظیم Webhook: {e}")

set_webhook()

# ====== مسیر Webhook برای دریافت پیام‌های تلگرام ======
@app.route('/webhook', methods=['POST'])
def webhook():
    if not bot_ready:
        return "ربات در حال آماده‌سازی...", 503
    try:
        data = request.get_json(force=True)
        logger.debug(f"Webhook received data: {data}")
        update = Update.de_json(data, bot_app.bot)

        # چک زنده بودن loop
        if not bot_loop or not bot_loop.is_running():
            logger.error("❌ bot_loop متوقف شده است!")
            return "loop dead", 500

        def handle_update_future(future):
            try:
                future.result()
                logger.info("✅ update processed successfully")
            except Exception as e:
                logger.error(f"❌ خطا در پردازش update: {e}", exc_info=True)

        future = asyncio.run_coroutine_threadsafe(bot_app.process_update(update), bot_loop)
        future.add_done_callback(handle_update_future)

        return 'OK', 200
    except Exception as e:
        logger.error(f"❌ خطا در webhook: {e}", exc_info=True)
        return 'Error', 500

# ====== صفحه اصلی ======
@app.route('/')
def index():
    return "ربات فعال است! 🤖"
