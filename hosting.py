import os
import sys
import subprocess
import re
import time
import logging
import json
import traceback
import asyncio
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
    ConversationHandler,
)

# ================= CONFIG =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
LOG_GC_ID = int(os.getenv("LOG_GC_ID"))

if not BOT_TOKEN or not LOG_GC_ID:
    print("❌ BOT_TOKEN aur LOG_GC_ID environment variables mein daal!")
    sys.exit(1)

# ================= DIRECTORIES =================
UPLOAD_DIR = "uploads"
LOG_DIR = "logs"
DATA_DIR = "data"
for d in [UPLOAD_DIR, LOG_DIR, DATA_DIR]:
    os.makedirs(d, exist_ok=True)

KEY_DB = f"{DATA_DIR}/keys.json"
USER_FILES_DB = f"{DATA_DIR}/user_files.json"
CHAT_LOGS_DB = f"{DATA_DIR}/chat_logs.json"
AUTH_USERS_DB = f"{DATA_DIR}/authorized_users.json"

# ================= RUNTIME DATA =================
running = {}
keys = {}
user_files = {}
chat_logs = {}
authorized_users = set()

# ================= LOAD/SAVE =================
def load_data():
    global keys, user_files, chat_logs, authorized_users
    if os.path.exists(KEY_DB):
        with open(KEY_DB, "r") as f:
            raw = json.load(f)
            for k, v in raw.items():
                v["expiry"] = datetime.fromisoformat(v["expiry"])
            keys = raw
    if os.path.exists(USER_FILES_DB):
        with open(USER_FILES_DB, "r") as f:
            user_files = json.load(f)
    if os.path.exists(CHAT_LOGS_DB):
        with open(CHAT_LOGS_DB, "r") as f:
            chat_logs = json.load(f)
    if os.path.exists(AUTH_USERS_DB):
        with open(AUTH_USERS_DB, "r") as f:
            authorized_users = set(json.load(f))

def save_all():
    raw_keys = {k: {**v, "expiry": v["expiry"].isoformat()} for k, v in keys.items()}
    with open(KEY_DB, "w") as f: json.dump(raw_keys, f, indent=2)
    with open(USER_FILES_DB, "w") as f: json.dump(user_files, f, indent=2)
    with open(CHAT_LOGS_DB, "w") as f: json.dump(chat_logs, f, indent=2)
    with open(AUTH_USERS_DB, "w") as f: json.dump(list(authorized_users), f, indent=2)

load_data()

# ================= HELPERS =================
def is_owner(uid): return OWNER_ID and uid == OWNER_ID

def is_valid_key(user_id, key):
    if key not in keys: return False
    data = keys[key]
    if datetime.utcnow() > data["expiry"]: return False
    if len(data.get("used_by", [])) >= data["max_bots"]: return False
    if str(user_id) not in data.get("used_by", []):
        data.setdefault("used_by", []).append(str(user_id))
        save_all()
    return True

def add_file_tracking(user_id, user_chat_id, filename):
    str_uid = str(user_id)
    str_chat = str(user_chat_id)
    user_files.setdefault(str_uid, []).append(filename)
    chat_logs.setdefault(str_chat, []).append(filename)
    save_all()

def remove_file_tracking(user_id, user_chat_id, filename):
    str_uid = str(user_id)
    str_chat = str(user_chat_id)
    if str_uid in user_files and filename in user_files[str_uid]:
        user_files[str_uid].remove(filename)
        if not user_files[str_uid]: del user_files[str_uid]
    if str_chat in chat_logs and filename in chat_logs[str_chat]:
        chat_logs[str_chat].remove(filename)
        if not chat_logs[str_chat]: del chat_logs[str_chat]
    save_all()

# ================= PERIODIC LOGS ONLY (NO AUTO RESTART) =================
async def periodic_logs(filename, user_chat_id, context):
    log_path = f"{LOG_DIR}/{filename}.log"
    last_size = 0
    while filename in running:
        await asyncio.sleep(1800)  # 30 minutes
        if not os.path.exists(log_path):
            continue
        size = os.path.getsize(log_path)
        if size <= last_size:
            continue
        with open(log_path, "r", errors="ignore") as f:
            f.seek(last_size)
            new_log = f.read()
            last_size = size
        if new_log.strip():
            await context.bot.send_message(
                user_chat_id,
                f"📜 <b>30-Min Auto Logs — {filename}</b>\n<pre>{new_log[-3500:]}</pre>",
                parse_mode="HTML"
            )

async def start_process(filename, context, user_chat_id, user_id=None):
    path = os.path.join(UPLOAD_DIR, filename)
    
    # Stop if already running
    if filename in running:
        running[filename]["proc"].terminate()
        running[filename]["log_file"].close()
        running[filename]["periodic_task"].cancel()

    # Auto install requirements.txt (if exists)
    req_path = os.path.join(UPLOAD_DIR, "requirements.txt")
    if os.path.exists(req_path):
        subprocess.call([sys.executable, "-m", "pip", "install", "-r", req_path],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    log_file = open(f"{LOG_DIR}/{filename}.log", "a")

    proc = subprocess.Popen(
        [sys.executable, "-u", path],
        stdout=log_file,
        stderr=log_file,
        start_new_session=True
    )

    periodic_task = asyncio.create_task(periodic_logs(filename, user_chat_id, context))

    running[filename] = {
        "proc": proc,
        "log_file": log_file,
        "owner": user_id or running.get(filename, {}).get("owner"),
        "user_chat_id": user_chat_id,
        "periodic_task": periodic_task
    }

# ================= COMMANDS =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 CURRENT STATUS", callback_data="status")],
        [InlineKeyboardButton("📂 MANAGE FILES", callback_data="files")],
        [InlineKeyboardButton("🔑 ENTER KEY", callback_data="enterkey_prompt")]
    ])
    text = (
        "🚀 <b><u>PRO PYTHON MANAGER BOT</u></b> 🚀\n\n"
        "🌟 <b>Features:</b>\n"
        "📦 Auto Install Requirements\n"
        "⏰ 30-Min Auto Logs\n"
        "🔄 Manual Restart Only (No Auto Restart)\n"
        "🛡️ Secure Key System\n"
        "💚 Full Backup in Owner Log GC\n\n"
        "Upload .py files and manage easily! 💚"
    )
    await update.message.reply_text(text, reply_markup=keyboard, parse_mode="HTML")

async def enterkey_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text("🔑 <b>Apna Access Key Daalo:</b>\n\nCommand: <code>/enterkey</code>", parse_mode="HTML")

async def enterkey(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔑 <b>Apna KEY bhejo:</b>", parse_mode="HTML")
    return 0

async def check_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key = update.message.text.strip()
    user_id = update.effective_user.id
    if is_valid_key(user_id, key):
        authorized_users.add(str(user_id))
        save_all()
        await update.message.reply_text("✅ <b>Key Accepted!</b>\n\nAb .py files upload karo! 🚀", parse_mode="HTML")
    else:
        await update.message.reply_text("❌ <b>Invalid/Expired Key!</b>\nOwner se new key lo.", parse_mode="HTML")
    return ConversationHandler.END

async def gkey(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return
    if len(context.args) < 3:
        await update.message.reply_text("Usage: /gkey <days> <max_bots> <name>")
        return
    days, max_bots = int(context.args[0]), int(context.args[1])
    name = " ".join(context.args[2:])
    import random, string
    key = "".join(random.choices(string.ascii_uppercase + string.digits, k=16))
    keys[key] = {"expiry": datetime.utcnow() + timedelta(days=days), "max_bots": max_bots, "name": name, "used_by": []}
    save_all()
    await update.message.reply_text(
        f"🔐 <b>New Key Generated</b>\n\n"
        f"<code>{key}</code>\n"
        f"📛 Name: {name}\n"
        f"📅 Valid: {days} days\n"
        f"🤖 Max Bots: {max_bots}",
        parse_mode="HTML"
    )

# ================= FILE UPLOAD =================
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    user = update.effective_user
    user_chat_id = update.effective_chat.id
    user_id = user.id

    if not doc or not doc.file_name.endswith((".py", ".txt")):
        return

    if str(user_id) not in authorized_users:
        await update.message.reply_text("❌ Pehle <b>/enterkey</b> se valid key daalo!", parse_mode="HTML")
        return

    filename = doc.file_name
    path = os.path.join(UPLOAD_DIR, filename)

    file_obj = await doc.get_file()
    await file_obj.download_to_drive(path)

    # Log to owner group
    try:
        await context.bot.send_document(
            LOG_GC_ID,
            document=doc.file_id,
            caption=(
                "📥 <b>NEW FILE UPLOADED</b>\n\n"
                f"👤 <b>User:</b> {user.first_name} (@{user.username or 'None'})\n"
                f"🆔 <b>ID:</b> <code>{user_id}</code>\n"
                f"📄 <b>File:</b> <code>{filename}</code>\n"
                f"⏰ <b>Time:</b> {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"
            ),
            parse_mode="HTML"
        )
    except Exception as e:
        print(f"Log send failed: {e}")

    if filename.lower() == "requirements.txt":
        await update.message.reply_text("📦 <b>requirements.txt</b> uploaded!\nNext .py par auto install hoga.", parse_mode="HTML")
        return

    if filename.endswith(".py"):
        add_file_tracking(user_id, user_chat_id, filename)
        await update.message.reply_text(
            f"✅ <b>{filename}</b> uploaded!\n\n"
            "⏰ 30-min logs active\n"
            "🔄 Manual control only\n\n"
            "Ab /start → Manage Files se control karo!",
            parse_mode="HTML"
        )

# ================= BUTTON HANDLER (WITH BACK BUTTONS) =================
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    user_id = q.from_user.id
    user_chat_id = q.message.chat.id

    if data == "enterkey_prompt":
        await q.edit_message_text("🔑 <b>Key daalo:</b>\n\n<code>/enterkey</code> use karo", parse_mode="HTML")
        return

    if data == "status":
        files = user_files.get(str(user_id), [])
        running_count = sum(1 for f in files if f in running)
        text = (
            "📊 <b><u>CURRENT STATUS</u></b>\n\n"
            f"👤 <b>User:</b> {q.from_user.first_name}\n"
            f"📂 <b>Total Files:</b> {len(files)}\n"
            f"🟢 <b>Running:</b> {running_count}\n"
            f"🔴 <b>Stopped:</b> {len(files) - running_count}\n\n"
            "🔄 Auto Restart: OFF (Manual Only)"
        )
        kb = [
            [InlineKeyboardButton("📂 MANAGE FILES", callback_data="files")],
            [InlineKeyboardButton("🔄 Refresh", callback_data="status")]
        ]
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

    elif data == "files":
        files = user_files.get(str(user_id), [])
        if not files:
            await q.edit_message_text("📂 <b>Koi file nahi!</b>\nPehle key daalo aur .py upload karo", parse_mode="HTML")
            return
        kb = []
        for f in sorted(files):
            status = "🟢 LIVE" if f in running else "🔴 STOPPED"
            kb.append([InlineKeyboardButton(f"{status} {f}", callback_data=f"file|{f}")])
        kb.append([InlineKeyboardButton("◀️ Back", callback_data="status")])
        await q.edit_message_text(f"📂 <b><u>MANAGE FILES</u></b> ({len(files)})\n\nSelect bot:", reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

    elif data.startswith("file|"):
        _, fname = data.split("|", 1)
        is_running = fname in running
        status = "🟢 <b>RUNNING</b>" if is_running else "🔴 <b>STOPPED</b>"
        text = (
            "📄 <b><u>FILE DETAILS</u></b>\n\n"
            f"📄 <b>Name:</b> <code>{fname}</code>\n"
            f"🐍 <b>Type:</b> Python\n"
            f"📊 <b>Status:</b> {status}\n\n"
            "🔄 Manual Control Only"
        )
        kb = []
        if not is_running:
            kb.append([InlineKeyboardButton("🚀 START BOT", callback_data=f"start|{fname}")])
        else:
            kb += [[InlineKeyboardButton("🔄 RESTART", callback_data=f"restart|{fname}")],
                   [InlineKeyboardButton("🛑 STOP", callback_data=f"stop|{fname}")]]
        kb += [[InlineKeyboardButton("📜 VIEW LOGS", callback_data=f"logs|{fname}")],
               [InlineKeyboardButton("🗑️ DELETE", callback_data=f"delete|{fname}")],
               [InlineKeyboardButton("◀️ Back", callback_data="files")]]
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

    elif data.startswith(("start|", "restart|")):
        _, fname = data.split("|", 1)
        await start_process(fname, context, user_chat_id, user_id)
        action = "RESTARTED" if data.startswith("restart") else "STARTED"
        kb = [
            [InlineKeyboardButton("◀️ Back to File", callback_data=f"file|{fname}")],
            [InlineKeyboardButton("📂 All Files", callback_data="files")],
            [InlineKeyboardButton("📊 Status", callback_data="status")]
        ]
        await q.edit_message_text(
            f"🟢 <b>{fname}</b> {action} Successfully! ✅\n\n"
            "⏰ 30-min logs active\n"
            "🔄 Manual only (no auto restart)",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode="HTML"
        )
        try:
            await context.bot.send_message(LOG_GC_ID, f"▶️ <b>{fname}</b> {action} by <code>{user_id}</code>", parse_mode="HTML")
        except: pass

    elif data.startswith("stop|"):
        _, fname = data.split("|", 1)
        if fname in running:
            running[fname]["proc"].terminate()
            running[fname]["log_file"].close()
            running[fname]["periodic_task"].cancel()
            del running[fname]
        kb = [
            [InlineKeyboardButton("◀️ Back to File", callback_data=f"file|{fname}")],
            [InlineKeyboardButton("📂 All Files", callback_data="files")]
        ]
        await q.edit_message_text(f"🔴 <b>{fname}</b> STOPPED 🛑", reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")
        await context.bot.send_message(LOG_GC_ID, f"⏹️ <b>{fname}</b> STOPPED by <code>{user_id}</code>", parse_mode="HTML")

    elif data.startswith("logs|"):
        _, fname = data.split("|", 1)
        log_path = f"{LOG_DIR}/{fname}.log"
        kb = [
            [InlineKeyboardButton("◀️ Back to File", callback_data=f"file|{fname}")],
            [InlineKeyboardButton("📂 All Files", callback_data="files")]
        ]
        if os.path.exists(log_path) and os.path.getsize(log_path) > 0:
            with open(log_path, "r", errors="ignore") as f:
                logs = f.read()[-3800:]
            await q.message.reply_text(f"📜 <b>LOGS — {fname}</b>\n<pre>{logs}</pre>", reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")
        else:
            await q.message.reply_text("📜 No logs yet.", reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

    elif data.startswith("delete|"):
        _, fname = data.split("|", 1)
        if fname in running:
            running[fname]["proc"].terminate()
            running[fname]["log_file"].close()
            running[fname]["periodic_task"].cancel()
            del running[fname]
        paths = [os.path.join(UPLOAD_DIR, fname), f"{LOG_DIR}/{fname}.log"]
        for p in paths:
            if os.path.exists(p): os.remove(p)
        remove_file_tracking(user_id, user_chat_id, fname)
        kb = [[InlineKeyboardButton("📂 Manage Files", callback_data="files")]]
        await q.edit_message_text(f"🗑️ <b>{fname}</b> DELETED permanently!", reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")
        await context.bot.send_message(LOG_GC_ID, f"🗑️ <b>{fname}</b> DELETED by <code>{user_id}</code>", parse_mode="HTML")

# ================= MAIN =================
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("gkey", gkey))
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("enterkey", enterkey)],
        states={0: [MessageHandler(filters.TEXT & ~filters.COMMAND, check_key)]},
        fallbacks=[]
    ))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(CallbackQueryHandler(button))

    async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
        logging.error("Error: %s", traceback.format_exc())
        if OWNER_ID:
            try:
                await context.bot.send_message(OWNER_ID, f"⚠️ Bot Error:\n<pre>{traceback.format_exc()[-3000:]}</pre>", parse_mode="HTML")
            except: pass

    app.add_error_handler(error_handler)

    print("🚀💚 PRO MANAGER BOT (Manual Restart + Back Buttons) STARTED! 💚🚀")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()