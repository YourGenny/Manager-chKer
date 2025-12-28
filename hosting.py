import os
import sys
import subprocess
import signal
import re
import time
import logging
import json
from datetime import datetime, timedelta

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ================= ENV =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))

LOG_GC_ID = -1003646548483   # 👈 YOUR GROUP / CHANNEL ID

if not BOT_TOKEN:
    print("BOT_TOKEN missing")
    while True:
        time.sleep(10)

# ================= DIRS =================
UPLOAD_DIR = "uploads"
LOG_DIR = "logs"
DATA_DIR = "data"

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

USER_DB_FILE = f"{DATA_DIR}/users.json"

# ================= LOGGING =================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(message)s",
    handlers=[
        logging.FileHandler("logs/manager.log"),
        logging.StreamHandler(sys.stdout)
    ]
)
log = logging.getLogger("MANAGER")

# ================= DATA =================
running = {}
keys = {}

# ================= USER DB =================
def load_users():
    if not os.path.exists(USER_DB_FILE):
        return {}
    with open(USER_DB_FILE, "r") as f:
        return json.load(f)

def save_users(data):
    with open(USER_DB_FILE, "w") as f:
        json.dump(data, f, indent=2)

users = load_users()

def save_user_info(user):
    uid = str(user.id)
    if uid not in users:
        users[uid] = {
            "id": user.id,
            "username": user.username,
            "first_name": user.first_name,
            "joined": datetime.utcnow().isoformat()
        }
        save_users(users)

# ================= HELPERS =================
def is_owner(uid):
    return OWNER_ID != 0 and uid == OWNER_ID

def extract_imports(file):
    imports = set()
    with open(file, "r", errors="ignore") as f:
        for line in f:
            m1 = re.match(r"^\s*import\s+([a-zA-Z0-9_]+)", line)
            m2 = re.match(r"^\s*from\s+([a-zA-Z0-9_]+)", line)
            if m1:
                imports.add(m1.group(1))
            if m2:
                imports.add(m2.group(1))

    std = {"os","sys","time","re","json","logging","asyncio","datetime","signal"}
    return imports - std

def pip_install(pkgs):
    for pkg in pkgs:
        log.info(f"Installing {pkg}")
        subprocess.call([sys.executable, "-m", "pip", "install", pkg])

def gen_key(days, max_bots, name):
    import random, string
    key = "".join(random.choices(string.ascii_uppercase + string.digits, k=16))
    keys[key] = {
        "expiry": datetime.utcnow() + timedelta(days=days),
        "max": max_bots,
        "name": name
    }
    return key

# ================= COMMANDS =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_info(update.effective_user)
    await update.message.reply_text(
        "🚀 **PRO MANAGER BOT ONLINE**\n\n"
        "📂 Upload `.py`\n"
        "🔑 `/gkey days bots name`\n"
        "📊 `/status`",
        parse_mode="Markdown"
    )

async def gkey(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return

    days = int(context.args[0])
    bots = int(context.args[1])
    name = " ".join(context.args[2:])

    key = gen_key(days, bots, name)

    await update.message.reply_text(
        f"🔐 **KEY GENERATED**\n\n"
        f"🗝 `{key}`\n"
        f"📛 {name}\n"
        f"📅 {days} days\n"
        f"🤖 {bots} bots",
        parse_mode="Markdown"
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (
        "📊 **STATUS**\n\n"
        f"👥 Users: {len(users)}\n"
        f"📂 Files: {len(os.listdir(UPLOAD_DIR))}\n"
        f"🟢 Running: {len(running)}"
    )
    await update.message.reply_text(txt, parse_mode="Markdown")

# ================= FILE UPLOAD =================
async def upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    save_user_info(user)

    doc = update.message.document
    if not doc or not doc.file_name.endswith(".py"):
        return

    path = os.path.join(UPLOAD_DIR, doc.file_name)
    await (await doc.get_file()).download_to_drive(path)

    # 🔁 Forward file to GC
    await context.bot.send_document(
        chat_id=LOG_GC_ID,
        document=doc.file_id,
        caption=(
            "📥 **FILE UPLOADED**\n\n"
            f"👤 User: {user.first_name}\n"
            f"🆔 UID: `{user.id}`\n"
            f"📄 File: `{doc.file_name}`\n"
            f"⏰ {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}"
        ),
        parse_mode="Markdown"
    )

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 HOST / START", callback_data=f"start|{doc.file_name}")],
        [InlineKeyboardButton("📜 LOGS", callback_data=f"logs|{doc.file_name}")]
    ])

    await update.message.reply_text(
        f"📄 `{doc.file_name}` uploaded successfully",
        reply_markup=kb,
        parse_mode="Markdown"
    )

# ================= BUTTON HANDLER =================
async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    action, fname = q.data.split("|", 1)
    path = os.path.join(UPLOAD_DIR, fname)

    if action == "start":
        if fname in running:
            return await q.answer("Already running", show_alert=True)

        pkgs = extract_imports(path)
        if pkgs:
            pip_install(pkgs)

        logf = open(f"{LOG_DIR}/{fname}.log", "a")

        proc = subprocess.Popen(
            [sys.executable, "-u", path],
            stdout=logf,
            stderr=logf,
            start_new_session=True
        )
        running[fname] = proc

        await q.edit_message_text(
            f"🟢 `{fname}` RUNNING\n📜 Logs enabled",
            parse_mode="Markdown"
        )

    elif action == "logs":
        log_path = f"{LOG_DIR}/{fname}.log"
        if not os.path.exists(log_path):
            return await q.answer("No logs", show_alert=True)

        with open(log_path, "r", errors="ignore") as f:
            data = f.read()[-3500:] or "Empty logs"

        await q.message.reply_text(
            f"📜 **LOGS – {fname}**\n```\n{data}\n```",
            parse_mode="Markdown"
        )

# ================= MAIN =================
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("gkey", gkey))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(MessageHandler(filters.Document.ALL, upload))
    app.add_handler(CallbackQueryHandler(buttons))

    log.info("🚀 Pro Manager Bot Started")
    app.run_polling()

if __name__ == "__main__":
    main()