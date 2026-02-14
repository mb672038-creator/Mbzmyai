# -*- coding: utf-8 -*-
import sys
import time
import threading
import asyncio
import os
import re
from collections import defaultdict
from pathlib import Path
from datetime import datetime

import requests
from PIL import Image
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from telegram.constants import ParseMode
import logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
# ====== دریافت متغیرهای محیطی ======
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
OCR_API_KEY = os.environ.get("OCR_API_KEY")

if not TELEGRAM_TOKEN or not GROQ_API_KEY:
    raise ValueError("لطفاً متغیرهای محیطی TELEGRAM_TOKEN و GROQ_API_KEY را تنظیم کنید.")

# ====== تنظیمات ======
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = "llama-3.3-70b-versatile"          # مدل قوی و رایگان
IMAGE_GEN_API = "https://image.pollinations.ai/prompt"  # ساخت عکس رایگان
OCR_URL = "https://api.ocr.space/parse/image"           # OCR رایگان
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "https://mbzmyai.onrender.com/webhook")  # آدرس Render شما
TIMEOUT = 60                               # برای اینترنت ضعیف

# پوشه ذخیره فایل‌ها (دسترسی در Render محدود است، از /tmp استفاده می‌کنیم)
DOWNLOAD_PATH = "/tmp/groqbot_files"
Path(DOWNLOAD_PATH).mkdir(parents=True, exist_ok=True)

app = Flask(__name__)

# تاریخچه مکالمات (در RAM)
user_history = defaultdict(list)

# ====== تشخیص زبان (ساده و بدون نیاز به کتابخانه اضافی) ======
def detect_language(text):
    persian_chars = set("ابپتثجچحخدذرزژسشصضطظعغفقکگلمنوهیئءآاًهٔ")
    if any(ch in persian_chars for ch in text):
        return "persian"
    return "english"

# ====== درخواست به Groq ======
def ask_groq(user_message, history):
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    lang = detect_language(user_message)
    system = "شما یک دستیار فارسی‌دان هستید. به فارسی پاسخ دهید." if lang == "persian" else "You are a helpful assistant. Answer in the same language as the user."
    messages = [{"role": "system", "content": system}] + history[-5:] + [{"role": "user", "content": user_message}]
    payload = {
        "model": MODEL,
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 2000
    }
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
            return "❌ کلید Groq نامعتبر است. لطفاً کلید جدید بسازید."
        elif r.status_code == 400:
            return "❌ درخواست نامعتبر (مدل را بررسی کنید)."
        else:
            return f"❌ خطای HTTP: {r.status_code}"
    except Exception as e:
        return f"❌ خطای غیرمنتظره: {type(e).__name__}"

# ====== ساخت عکس با هوش مصنوعی (رایگان) ======
def generate_image(prompt: str) -> str:
    """ساخت عکس و برگرداندن مسیر فایل ذخیره شده"""
    try:
        # پالایش پرامپت
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
        print(f"خطا در ساخت عکس: {e}", file=sys.stderr)
        return None

# ====== خواندن متن عکس (OCR) ======
def extract_text_from_image(image_path: str) -> str:
    """ارسال عکس به OCR.space و دریافت متن"""
    if not OCR_API_KEY:
        return "🔑 کلید OCR.space تنظیم نشده است. لطفاً متغیر محیطی OCR_API_KEY را مقداردهی کنید."
    try:
        with open(image_path, 'rb') as f:
            files = {'file': f}
            data = {'apikey': OCR_API_KEY, 'language': 'per', 'isOverlayRequired': False}
            response = requests.post(OCR_URL, files=files, data=data, timeout=TIMEOUT)
            result = response.json()
            if result.get('IsErroredOnProcessing') is False:
                return result['ParsedResults'][0]['ParsedText']
            else:
                return "❌ متنی در عکس یافت نشد یا خوانایی نداشت."
    except Exception as e:
        return f"❌ خطای OCR: {type(e).__name__}"

# ====== هندلر فرمان /start با دکمه ======
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("✅ start handler executed", file=sys.stderr)
    print("✅ تابع start اجرا شد.", file=sys.stderr)
    keyboard = [[InlineKeyboardButton("🧹 پاک کردن تاریخچه", callback_data="clear")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    welcome_text = (
        "🤖 **ربات فوق‌پیشرفته Groq**\n\n"
        "✨ **قابلیت‌های ویژه:**\n"
        "• پاسخ به هر سوالی با هوش مصنوعی Groq (کدنویسی، ریاضی، مشاوره و ...)\n"
        "• ساخت عکس با هوش مصنوعی – فقط کافی است بگویید «عکس ... بساز»\n"
        "• خواندن متن عکس‌ها (OCR) – با ارسال عکس\n"
        "• ذخیره همه نوع فایل (عکس، فیلم، صدا، سند و ...)\n"
        "• یادگیری مکالمات قبلی شما\n"
        "• تایم‌اوت ۶۰ ثانیه – مناسب اینترنت ضعیف\n\n"
        "💬 **هر سوالی داری بپرس، من پاسخ می‌دم.**"
    )
    await update.message.reply_text(welcome_text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)

# ====== هندلر دکمه‌ها ======
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "clear":
        user_id = update.effective_user.id
        if user_id in user_history:
            user_history[user_id].clear()
        await query.edit_message_text("🧹 تاریخچه مکالمه شما پاک شد.")

# ====== هندلر پیام‌های متنی (پاسخگویی هوشمند + تشخیص ساخت عکس) ======
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("✅ handle_text executed", file=sys.stderr)
    user_id = update.effective_user.id
    text = update.message.text

    # --- تشخیص درخواست ساخت عکس ---
    if any(keyword in text for keyword in ["عکس", "تصویر", "بساز", "draw", "image", "picture", "generate"]):
        # استخراج پرامپت
        prompt = re.sub(r'(عکس|تصویر|بساز|draw|image|picture|generate|of|a|an|یک|یه)\s*', '', text, flags=re.IGNORECASE).strip()
        if not prompt:
            prompt = text
        thinking = await update.message.reply_text("🎨 در حال ساخت عکس... لطفاً صبر کنید (حدود ۱۵ ثانیه)")
        img_path = generate_image(prompt)
        if img_path and os.path.exists(img_path):
            with open(img_path, 'rb') as f:
                await update.message.reply_photo(photo=InputFile(f), caption=f"🖼️ عکس ساخته شده برای: {prompt}")
            await thinking.delete()
        else:
            await thinking.edit_text("❌ ساخت عکس با خطا مواجه شد. دوباره امتحان کنید.")
        return

    # --- پاسخ عادی با Groq ---
    user_history[user_id].append({"role": "user", "content": text})
    thinking = await update.message.reply_text("⏳ در حال فکر کردن...")
    answer = ask_groq(text, user_history[user_id][:-1])
    user_history[user_id].append({"role": "assistant", "content": answer})
    await thinking.edit_text(answer)

# ====== هندلر عکس (OCR خودکار + ذخیره) ======
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo = update.message.photo[-1]  # بزرگترین سایز
    file = await context.bot.get_file(photo.file_id)
    timestamp = int(time.time())
    filename = f"{DOWNLOAD_PATH}/photo_{timestamp}.jpg"
    await file.download_to_drive(filename)
    await update.message.reply_text(f"🖼️ عکس دریافت و ذخیره شد:\n{filename}")

    # OCR خودکار
    ocr_text = extract_text_from_image(filename)
    if ocr_text and "❌" not in ocr_text and "🔑" not in ocr_text:
        await update.message.reply_text(f"📄 **متن استخراج‌شده از عکس:**\n```\n{ocr_text}\n```", parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(ocr_text)

# ====== هندلر فیلم ======
async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    video = update.message.video
    file = await context.bot.get_file(video.file_id)
    timestamp = int(time.time())
    filename = f"{DOWNLOAD_PATH}/video_{timestamp}.mp4"
    await file.download_to_drive(filename)
    await update.message.reply_text(f"🎬 فیلم دریافت و ذخیره شد:\n{filename}")

# ====== هندلر صدا ======
async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    voice = update.message.voice
    file = await context.bot.get_file(voice.file_id)
    timestamp = int(time.time())
    filename = f"{DOWNLOAD_PATH}/voice_{timestamp}.ogg"
    await file.download_to_drive(filename)
    await update.message.reply_text(f"🎤 پیام صوتی دریافت و ذخیره شد:\n{filename}")

# ====== هندلر سند ======
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    file = await context.bot.get_file(doc.file_id)
    timestamp = int(time.time())
    ext = os.path.splitext(doc.file_name)[1] if doc.file_name else ".bin"
    ext = ext.lstrip(".")
    filename = f"{DOWNLOAD_PATH}/doc_{timestamp}.{ext}"
    await file.download_to_drive(filename)
    await update.message.reply_text(f"📄 فایل دریافت و ذخیره شد:\n{filename}")

# ====== هندلر استیکر ======
async def handle_sticker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sticker = update.message.sticker
    if sticker.is_animated or sticker.is_video:
        await update.message.reply_text("🎭 استیکر متحرک دریافت شد (قابلیت ذخیره ندارد)")
    else:
        file = await context.bot.get_file(sticker.file_id)
        timestamp = int(time.time())
        filename = f"{DOWNLOAD_PATH}/sticker_{timestamp}.png"
        await file.download_to_drive(filename)
        await update.message.reply_text(f"🎭 استیکر دریافت و ذخیره شد:\n{filename}")

# ====== هندلر سایر فایل‌ها ======
async def handle_unknown_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📦 فایل دریافت شد. متأسفانه این نوع فایل قابل پردازش نیست.")

# ====== خطاهای عمومی ======
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print(f"⚠️ خطا در ربات: {context.error}", file=sys.stderr)
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
        print("✅ ربات آماده شد.", file=sys.stderr)
        loop.run_forever()
    except Exception as e:
        print(f"❌ خطا در run_bot: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)

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
            print("✅ Webhook تنظیم شد.", file=sys.stderr)
        else:
            print(f"⚠️ Webhook تنظیم نشد: {resp.text}", file=sys.stderr)
    except Exception as e:
        print(f"⚠️ خطا در تنظیم Webhook: {e}", file=sys.stderr)

set_webhook()

# ====== مسیر Webhook برای دریافت پیام‌های تلگرام ======
@app.route('/webhook', methods=['POST'])
def webhook():
    if not bot_ready:
        return "ربات در حال آماده‌سازی...", 503
    try:
        data = request.get_json(force=True)
        update = Update.de_json(data, bot_app.bot)
        
        # تعریف تابع callback برای گرفتن خطاهای احتمالی
        def handle_update_future(future):
            try:
                future.result()  # اگر خطایی باشد، اینجا رخ می‌دهد
            except Exception as e:
                print(f"❌ خطا در پردازش update: {e}", file=sys.stderr)
                import traceback
                traceback.print_exc(file=sys.stderr)
        
        # ایجاد future و افزودن callback
        future = asyncio.run_coroutine_threadsafe(bot_app.process_update(update), bot_loop)
        future.add_done_callback(handle_update_future)
        
        return 'OK', 200
    except Exception as e:
        print(f"❌ خطا در webhook: {e}", file=sys.stderr)
        return 'Error', 500

# ====== صفحه اصلی (برای تست و UptimeRobot) ======
@app.route('/')
def index():
    return "ربات فعال است! 🤖"
if __name__ == "__main__":
    app.run()
