import os
import subprocess
import signal
from datetime import datetime, timedelta, date
import random
import string

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ============ CONFIG (SAFE) ============
MANAGER_TOKEN = os.getenv("BOT_TOKEN")  # Railway ENV
OWNER_ID = int(os.getenv("OWNER_ID", "0"))  # SAFE DEFAULT

UPLOAD_DIR = "uploads"
MAX_BOTS = 10
KEY_LENGTH = 16
# ======================================

if not MANAGER_TOKEN:
    raise RuntimeError("❌ BOT_TOKEN not set in environment variables")

if OWNER_ID == 0:
    print("⚠️ WARNING: OWNER_ID not set, owner-only features disabled")

os.makedirs(UPLOAD_DIR, exist_ok=True)

# running bots: filename -> info
running = {}

# keys: KEY -> expiry_date
keys = {}

# ---------- HELPERS ----------
def is_owner(user_id: int) -> bool:
    return OWNER_ID != 0 and user_id == OWNER_ID


def generate_key(length=KEY_LENGTH):
    chars = string.ascii_uppercase + string.digits
    return ''.join(random.choices(chars, k=length))


def key_valid(key: str):
    key = key.strip().upper()
    expiry = keys.get(key)
    if not expiry:
        return False, "❌ INVALID KEY"

    today = datetime.utcnow().date()
    if today > expiry:
        return False, "❌ KEY EXPIRED"

    return True, expiry


def any_valid_key():
    today = datetime.utcnow().date()
    valid = [(k, d) for k, d in keys.items() if d >= today]
    if not valid:
        return None
    valid.sort(key=lambda x: x[1], reverse=True)
    return valid[0][0]

# ---------- COMMANDS ----------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✅ Manager Bot ONLINE\n\n"
        "• Upload .py file (OWNER only)\n"
        "• /genkey <days>\n"
        "• /runkey <KEY> <file.py>\n"
        "• /stopkey <KEY> <file.py>"
    )


async def genkey_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("❌ Not allowed")
        return

    if len(context.args) != 1:
        await update.message.reply_text("Usage: /genkey <days>")
        return

    try:
        days = int(context.args[0])
        if days <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Days must be positive number")
        return

    key = generate_key()
    expiry = datetime.utcnow().date() + timedelta(days=days)
    keys[key] = expiry

    await update.message.reply_text(
        f"🔑 KEY GENERATED\n\nKEY: `{key}`\nExpiry: `{expiry}`",
        parse_mode="Markdown"
    )


async def runkey_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("❌ Not allowed")
        return

    if len(context.args) != 2:
        await update.message.reply_text("Usage: /runkey <KEY> <file.py>")
        return

    key, filename = context.args
    ok, msg = key_valid(key)
    if not ok:
        await update.message.reply_text(msg)
        return

    path = os.path.join(UPLOAD_DIR, filename)
    if not os.path.isfile(path):
        await update.message.reply_text("❌ File not found")
        return

    if filename in running:
        await update.message.reply_text("⚠️ Already running")
        return

    proc = subprocess.Popen(
        ["python", "-u", path],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True
    )

    running[filename] = {
        "process": proc,
        "key": key,
        "started": datetime.utcnow()
    }

    await update.message.reply_text(f"✅ `{filename}` RUNNING", parse_mode="Markdown")


async def stopkey_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("❌ Not allowed")
        return

    if len(context.args) != 2:
        await update.message.reply_text("Usage: /stopkey <KEY> <file.py>")
        return

    key, filename = context.args
    ok, msg = key_valid(key)
    if not ok:
        await update.message.reply_text(msg)
        return

    bot = running.get(filename)
    if not bot or bot["key"] != key:
        await update.message.reply_text("❌ Bot not running or wrong key")
        return

    try:
        os.killpg(os.getpgid(bot["process"].pid), signal.SIGTERM)
    except Exception:
        bot["process"].terminate()

    del running[filename]
    await update.message.reply_text(f"🛑 `{filename}` STOPPED", parse_mode="Markdown")


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not running:
        await update.message.reply_text("No bots running")
        return

    text = "🟢 Running Bots:\n\n"
    for f, i in running.items():
        text += f"- {f}\n"
    await update.message.reply_text(text)


# ---------- FILE UPLOAD ----------
async def file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("❌ Not allowed")
        return

    doc = update.message.document
    if not doc or not doc.file_name.endswith(".py"):
        await update.message.reply_text("❌ Only .py files allowed")
        return

    path = os.path.join(UPLOAD_DIR, doc.file_name)
    await (await doc.get_file()).download_to_drive(path)

    await update.message.reply_text(
        f"📄 `{doc.file_name}` uploaded\n"
        f"Use:\n/runkey <KEY> {doc.file_name}",
        parse_mode="Markdown"
    )


# ---------- MAIN ----------
def main():
    app = Application