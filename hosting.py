import os
import subprocess
import signal
import time
from datetime import datetime, timedelta

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

print("🚀 Container booting...")

# ===== ENV =====
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))

if not BOT_TOKEN:
    print("❌ BOT_TOKEN missing")
    while True:
        time.sleep(10)

print("✅ BOT_TOKEN loaded")
print("OWNER_ID =", OWNER_ID)

# ===== CONFIG =====
UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

running = {}   # filename -> process
keys = {}      # key -> expiry_date

# ===== HELPERS =====
def is_owner(uid: int) -> bool:
    return OWNER_ID != 0 and uid == OWNER_ID

def gen_key(days: int):
    import random, string
    key = "".join(random.choices(string.ascii_uppercase + string.digits, k=16))
    keys[key] = datetime.utcnow().date() + timedelta(days=days)
    return key

def key_valid(key: str):
    key = key.upper()
    if key not in keys:
        return False
    return datetime.utcnow().date() <= keys[key]

# ===== COMMANDS =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ Manager Bot Running")

async def genkey(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return await update.message.reply_text("❌ Not allowed")

    if len(context.args) != 1:
        return await update.message.reply_text("/genkey <days>")

    days = int(context.args[0])
    key = gen_key(days)
    await update.message.reply_text(f"🔑 KEY:\n{key}")

async def runkey(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return await update.message.reply_text("❌ Not allowed")

    if len(context.args) != 2:
        return await update.message.reply_text("/runkey <KEY> <file.py>")

    key, fname = context.args
    if not key_valid(key):
        return await update.message.reply_text("❌ Invalid/Expired key")

    path = os.path.join(UPLOAD_DIR, fname)
    if not os.path.isfile(path):
        return await update.message.reply_text("❌ File not found")

    if fname in running:
        return await update.message.reply_text("⚠️ Already running")

    proc = subprocess.Popen(
        ["python", "-u", path],
        start_new_session=True
    )
    running[fname] = proc
    await update.message.reply_text(f"▶ {fname} started")

async def stopkey(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return await update.message.reply_text("❌ Not allowed")

    if len(context.args) != 2:
        return await update.message.reply_text("/stopkey <KEY> <file.py>")

    key, fname = context.args
    if not key_valid(key):
        return await update.message.reply_text("❌ Invalid/Expired key")

    proc = running.get(fname)
    if not proc:
        return await update.message.reply_text("❌ Not running")

    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except Exception:
        proc.terminate()

    del running[fname]
    await update.message.reply_text(f"⏹ {fname} stopped")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not running:
        return await update.message.reply_text("No bots running")

    text = "🟢 Running:\n"
    for f in running:
        text += f"- {f}\n"
    await update.message.reply_text(text)

# ===== FILE UPLOAD =====
async def upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return await update.message.reply_text("❌ Not allowed")

    doc = update.message.document
    if not doc or not doc.file_name.endswith(".py"):
        return await update.message.reply_text("❌ Only .py allowed")

    path = os.path.join(UPLOAD_DIR, doc.file_name)
    await (await doc.get_file()).download_to_drive(path)

    await update.message.reply_text(f"📄 Uploaded {doc.file_name}")

# ===== MAIN =====
def main():
    print("🚀 Starting bot process...")
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("genkey", genkey))
    app.add_handler(CommandHandler("runkey", runkey))
    app.add_handler(CommandHandler("stopkey", stopkey))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(MessageHandler(filters.Document.ALL, upload))

    print("✅ Manager bot started")
    app.run_polling()

if __name__ == "__main__":
    main()