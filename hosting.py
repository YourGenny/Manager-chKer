"""
Manager bot:
- Upload .py files (only OWNER can)
- Buttons: Run, Stop, Run (AUTO KEY)
- /genkey <days> -> generates CAPITAL key, stores expiry as DATE only (YYYY-MM-DD)
- /runkey <KEY> <filename>  -> run using provided KEY (validates expiry)
- /stopkey <KEY> <filename> -> stop using provided KEY
- /status -> show running bots
"""

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

# ============ CONFIG =============
MANAGER_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID"))                         # <-- your TG id
UPLOAD_DIR = "uploads"
MAX_BOTS = 10
KEY_LENGTH = 16
# ==================================

os.makedirs(UPLOAD_DIR, exist_ok=True)

# running: filename -> dict(process=proc, key_used=KEY or None, started_at=datetime)
running = {}

# keys: KEY (CAPS) -> expiry_date (datetime.date)
keys = {}

# ---------- HELPERS ----------
def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID

def generate_key(length=KEY_LENGTH):
    chars = string.ascii_uppercase + string.digits
    return ''.join(random.choices(chars, k=length))

def key_valid(key: str):
    """Return (True, expiry_date) if valid, else (False, reason_str)."""
    k = key.strip().upper()
    expiry = keys.get(k)
    if not expiry:
        return False, "❌ INVALID KEY"
    today = date.utcnow() if hasattr(date, "utcnow") else datetime.utcnow().date()
    # use datetime.utcnow().date() for portability:
    today = datetime.utcnow().date()
    if today > expiry:
        return False, "❌ KEY EXPIRED"
    return True, expiry

def any_valid_key():
    """Return a valid key (most recently created) or None."""
    today = datetime.utcnow().date()
    # choose key with max expiry that is >= today
    valid_items = [(k, d) for k, d in keys.items() if d >= today]
    if not valid_items:
        return None
    # pick key with latest expiry (for convenience)
    valid_items.sort(key=lambda x: x[1], reverse=True)
    return valid_items[0][0]

# ---------- COMMANDS ----------

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Manager bot ONLINE ✅\n\n"
        "Send me a .py file to upload (OWNER only). Use /genkey <days> to create keys."
    )

# genkey
async def genkey_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("❌ Not allowed")
        return

    if len(context.args) != 1:
        await update.message.reply_text("Usage:\n/genkey <days>\nexample: /genkey 7")
        return

    try:
        days = int(context.args[0])
        if days <= 0:
            raise ValueError
    except:
        await update.message.reply_text("❌ days must be positive integer")
        return

    key = generate_key()
    expiry_date = datetime.utcnow().date() + timedelta(days=days)
    keys[key] = expiry_date

    await update.message.reply_text(
        f"🔑 KEY GENERATED\n\nKEY: `{key}`\nExpiry Date: `{expiry_date}`",
        parse_mode="Markdown"
    )

# runkey
async def runkey_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("❌ Not allowed")
        return

    if len(context.args) != 2:
        await update.message.reply_text("Usage:\n/runkey <KEY> <filename.py>")
        return

    key = context.args[0].strip().upper()
    filename = context.args[1].strip()

    ok, info = key_valid(key)
    if not ok:
        await update.message.reply_text(info)
        return

    file_path = os.path.join(UPLOAD_DIR, filename)
    if not os.path.isfile(file_path):
        await update.message.reply_text("❌ File not found in uploads")
        return

    if filename in running:
        await update.message.reply_text("⚠️ Bot already running")
        return

    if len(running) >= MAX_BOTS:
        await update.message.reply_text("⚠️ Global max bots reached")
        return

    msg = await update.message.reply_text(f"🚀 Starting {filename} using KEY `{key}`...", parse_mode="Markdown")
    # spawn process
    proc = subprocess.Popen(
        ["python", file_path],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True
    )
    running[filename] = {"process": proc, "key_used": key, "started_at": datetime.utcnow()}
    await msg.edit_text(f"✅ `{filename}` RUNNING (KEY: `{key}`)", parse_mode="Markdown")


# stopkey
async def stopkey_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("❌ Not allowed")
        return

    if len(context.args) != 2:
        await update.message.reply_text("Usage:\n/stopkey <KEY> <filename.py>")
        return

    key = context.args[0].strip().upper()
    filename = context.args[1].strip()

    ok, info = key_valid(key)
    if not ok:
        await update.message.reply_text(info)
        return

    entry = running.get(filename)
    if not entry:
        await update.message.reply_text("❌ Bot not running")
        return

    # only allow stop if same key was used or owner (we are owner)
    # We'll allow stop if provided key matches the key_used for that bot
    if entry.get("key_used") != key:
        await update.message.reply_text("❌ Provided key did not start this bot")
        return

    proc = entry["process"]
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except Exception:
        try:
            proc.terminate()
        except Exception:
            pass

    del running[filename]
    await update.message.reply_text(f"🛑 `{filename}` stopped", parse_mode="Markdown")


# status
async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not running:
        await update.message.reply_text("No bots running")
        return

    text = "🟢 Running bots:\n\n"
    for fn, info in running.items():
        started = info.get("started_at").strftime("%Y-%m-%d %H:%M:%S")
        key_used = info.get("key_used")
        text += f"- {fn}  (key: {key_used})\n"
    await update.message.reply_text(text)

# ---------- FILE UPLOAD & BUTTONS ----------

async def file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("❌ Not allowed")
        return

    doc = update.message.document
    if not doc:
        await update.message.reply_text("No document found")
        return

    if not doc.file_name.endswith(".py"):
        await update.message.reply_text("❌ Only .py files allowed")
        return

    filename = doc.file_name
    path = os.path.join(UPLOAD_DIR, filename)

    file_obj = await doc.get_file()
    await file_obj.download_to_drive(path)

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("▶ Run (AUTO KEY)", callback_data=f"run_auto|{filename}"),
            InlineKeyboardButton("▶ Run (use /runkey)", callback_data=f"run_req|{filename}")
        ],
        [
            InlineKeyboardButton("⏹ Stop (use /stopkey)", callback_data=f"stop_req|{filename}"),
            InlineKeyboardButton("📄 Filename", callback_data=f"noop|{filename}")
        ]
    ])

    await update.message.reply_text(
        f"📄 Uploaded: `{filename}`\nChoose action:\n- Use `Run (AUTO KEY)` to auto-use a valid key if present\n- Or run with explicit: `/runkey <KEY> {filename}`",
        reply_markup=kb,
        parse_mode="Markdown"
    )


# ---------- BUTTON CALLBACK HANDLER ----------

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    parts = data.split("|")
    if len(parts) != 2:
        await query.edit_message_text("Invalid action")
        return

    action, filename = parts
    path = os.path.join(UPLOAD_DIR, filename)

    if action == "run_auto":
        if filename in running:
            await query.edit_message_text("⚠️ Already running")
            return

        if not os.path.isfile(path):
            await query.edit_message_text("❌ File not found")
            return

        if len(running) >= MAX_BOTS:
            await query.edit_message_text("⚠️ Max bots reached")
            return

        key = any_valid_key()
        if not key:
            await query.edit_message_text(
                "❌ No valid key available. Use /genkey <days> to create one, then run with /runkey <KEY> <filename>"
            )
            return

        # start process with selected key
        proc = subprocess.Popen(
            ["python", path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True
        )
        running[filename] = {"process": proc, "key_used": key, "started_at": datetime.utcnow()}
        await query.edit_message_text(f"✅ `{filename}` RUNNING (KEY: `{key}`)", parse_mode="Markdown")

    elif action == "run_req":
        # inform user to use /runkey
        await query.edit_message_text(
            f"Use this command to run:\n`/runkey <KEY> {filename}`\nGet keys with `/genkey <days>`",
            parse_mode="Markdown"
        )

    elif action == "stop_req":
        await query.edit_message_text(
            f"Use this command to stop:\n`/stopkey <KEY> {filename}`",
            parse_mode="Markdown"
        )

    elif action == "noop":
        await query.edit_message_text(f"File: `{filename}`", parse_mode="Markdown")
    else:
        await query.edit_message_text("Unknown action")


# ---------- MAIN ----------

def main():
    app = ApplicationBuilder().token(MANAGER_TOKEN).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("genkey", genkey_cmd))
    app.add_handler(CommandHandler("runkey", runkey_cmd))
    app.add_handler(CommandHandler("stopkey", stopkey_cmd))
    app.add_handler(CommandHandler("status", status_cmd))

    app.add_handler(MessageHandler(filters.Document.ALL, file_handler))
    app.add_handler(CallbackQueryHandler(button_handler))

    print("Manager bot started")
    app.run_polling()

if __name__ == "__main__":
    main()