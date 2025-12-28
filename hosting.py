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
LOG_GC_ID = int(os.getenv("LOG_GC_ID", "0"))  # Tera personal log group/channel ID (must be negative)

if not BOT_TOKEN or not LOG_GC_ID:
    print("❌ BOT_TOKEN aur LOG_GC_ID dono environment variables mein daal!")
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

# ================= RUNTIME DATA =================
running = {}        # filename: {proc, log_file, owner, user_chat_id, monitor_task, periodic_task}
keys = {}
user_files = {}     # str(user_id): [filenames]
chat_logs = {}      # str(chat_id): [filenames]

# ================= LOAD/SAVE DATA =================
def load_data():
    global keys, user_files, chat_logs
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

def save_all():
    raw_keys = {k: {**v, "expiry": v["expiry"].isoformat()} for k, v in keys.items()}
    with open(KEY_DB, "w") as f: json.dump(raw_keys, f, indent=2)
    with open(USER_FILES_DB, "w") as f: json.dump(user_files, f, indent=2)
    with open(CHAT_LOGS_DB, "w") as f: json.dump(chat_logs, f, indent=2)

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

# ================= PROCESS MONITOR & AUTO LOGS =================
async def monitor_and_restart(filename, proc, log_file, user_chat_id, context):
    while True:
        await asyncio.sleep(5)
        if proc.poll() is not None:
            log_file.close()
            await context.bot.send_message(LOG_GC_ID, f"🔴 <b>CRASH:</b> <code>{filename}</code> | Auto Restarting...", parse_mode="HTML")
            await context.bot.send_message(user_chat_id, f"⚠️ <b>{filename}</b> crashed!\n🔄 Auto restarting...", parse_mode="HTML")
            await start_process(filename, context, user_chat_id)

async def periodic_logs(filename, user_chat_id, context):
    log_path = f"{LOG_DIR}/{filename}.log"
    last_size = 0
    while filename in running:
        await asyncio.sleep(1800)  # 30 minutes
        if not os.path.exists(log_path): continue
        size = os.path.getsize(log_path)
        if size <= last_size: continue
        with open(log_path, "r", errors="ignore") as f:
            f.seek(last_size)
            new_log = f.read()
            last_size = size
        if new_log.strip():
            await context.bot.send_message(user_chat_id, f"📜 <b>30-Min Auto Logs — {filename}</b>\n<pre>{new_log[-3500:]}</pre>", parse_mode="HTML")

async def start_process(filename, context, user_chat_id, user_id=None):
    path = os.path.join(UPLOAD_DIR, filename)
    if filename in running:
        running[filename]["proc"].terminate()
        running[filename]["log_file"].close()
        running[filename]["monitor_task"].cancel()
        running[filename]["periodic_task"].cancel()

    # Auto install requirements
    req_path = os.path.join(UPLOAD_DIR, "requirements.txt")
    if os.path.exists(req_path):
        subprocess.call([sys.executable, "-m", "pip", "install", "-r", req_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    log_file = open(f"{LOG_DIR}/{filename}.log", "a")
    proc = subprocess.Popen([sys.executable, "-u", path], stdout=log_file, stderr=log_file, cwd=UPLOAD_DIR, start_new_session=True)

    monitor_task = asyncio.create_task(monitor_and_restart(filename, proc, log_file, user_chat_id, context))
    periodic_task = asyncio.create_task(periodic_logs(filename, user_chat_id, context))

    running[filename] = {
        "proc": proc, "log_file": log_file, "owner": user_id or running.get(filename, {}).get("owner"),
        "user_chat_id": user_chat_id, "monitor_task": monitor_task, "periodic_task": periodic_task
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
        "🌟 <b>Premium Features Active:</b>\n"
        "✨ Auto Restart on Crash\n"
        "📦 Auto Install Requirements\n"
        "⏰ 30-Min Auto Logs\n"
        "🔄 Live Monitoring\n"
        "🛡️ Secure Key System\n\n"
        "💚 <i>Bot hare hare, performance top-class!</i> 💚\n\n"
        "Upload .py files aur full control lo!"
    )

    await update.message.reply_text(text, reply_markup=keyboard, parse_mode="HTML")

async def enterkey_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        "🔑 <b>Enter Your Access Key</b>\n\n"
        "Command: <code>/enterkey</code> use karo\n"
        "Ya direct key message karo\n\n"
        "<i>Owner se /gkey se key lo</i>",
        parse_mode="HTML"
    )

async def enterkey(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔑 <b>Apna KEY bhejo:</b>", parse_mode="HTML")
    return 0

async def check_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key = update.message.text.strip()
    if is_valid_key(update.effective_user.id, key):
        await update.message.reply_text("✅ <b>Key Accepted!</b>\nAb .py files upload kar sakte ho! 🚀", parse_mode="HTML")
    else:
        await update.message.reply_text("❌ <b>Invalid ya Expired Key!</b>\nOwner se new key lo.", parse_mode="HTML")
    return ConversationHandler.END

async def gkey(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return
    if len(context.args) < 3:
        await update.message.reply_text("/gkey <days> <max_bots> <name>")
        return
    days, max_bots = int(context.args[0]), int(context.args[1])
    name = " ".join(context.args[2:])
    import random, string
    key = "".join(random.choices(string.ascii_uppercase + string.digits, k=16))
    keys[key] = {"expiry": datetime.utcnow() + timedelta(days=days), "max_bots": max_bots, "name": name, "used_by": []}
    save_all()
    await update.message.reply_text(f"🔐 <b>New Key Generated</b>\n<code>{key}</code>\n📅 {days} days | 🤖 {max_bots} bots", parse_mode="HTML")

# ================= FILE UPLOAD =================
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    user = update.effective_user
    user_chat_id = update.effective_chat.id

    if not doc or not doc.file_name.endswith((".py", ".txt")): return
    if str(user.id) not in user_files:
        await update.message.reply_text("❌ Pehle <b>/enterkey</b> se valid key daalo!", parse_mode="HTML")
        return

    filename = doc.file_name
    path = os.path.join(UPLOAD_DIR, filename)
    await doc.get_file().download_to_drive(path)

    # Log to owner GC
    await context.bot.send_document(
        LOG_GC_ID,
        document=doc.file_id,
        caption=(
            "📥 <b>NEW FILE UPLOADED</b>\n\n"
            f"👤 <b>User:</b> {user.first_name} (@{user.username or 'None'})\n"
            f"🆔 <b>ID:</b> <code>{user.id}</code>\n"
            f"📄 <b>File:</b> <code>{filename}</code>\n"
            f"⏰ <b>Time:</b> {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"
        ),
        parse_mode="HTML"
    )

    if filename.lower() == "requirements.txt":
        await update.message.reply_text("📦 <b>requirements.txt</b> uploaded! Next .py upload par auto install hoga.", parse_mode="HTML")
        return

    if filename.endswith(".py"):
        add_file_tracking(user.id, user_chat_id, filename)
        await update.message.reply_text(
            f"✅ <b>{filename}</b> uploaded successfully!\n\n"
            "🌟 Auto features active:\n"
            "🔄 Crash par restart\n"
            "⏰ Har 30 min logs\n"
            "🛡️ Backup mere log GC mein\n\n"
            "/start → Manage Files se control karo!",
            parse_mode="HTML"
        )

# ================= BUTTON HANDLER =================
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    user_id = q.from_user.id
    user_chat_id = q.message.chat.id

    if data == "status":
        files = user_files.get(str(user_id), [])
        running_count = sum(1 for f in files if f in running)
        text = (
            "📊 <b><u>CURRENT STATUS</u></b>\n\n"
            f"👤 <b>User :</b> {q.from_user.first_name} {'👑' if is_owner(user_id) else '🧑‍💻'}\n"
            f"🆔 <b>ID :</b> <code>{user_id}</code>\n\n"
            f"📂 <b>Total Files :</b> {len(files)}\n"
            f"🟢 <b>Running     :</b> {running_count}\n"
            f"🔴 <b>Stopped     :</b> {len(files) - running_count}\n\n"
            f"💎 <b>Premium     :</b> ❌ <i>Inactive</i>\n"
            f"🔓 <b>Bot Status  :</b> 🟢 <b>UNLOCKED</b>\n"
            f"🔥 <b>Force Join  :</b> ✅ <b>ENABLED</b>\n\n"
            "💚 <i>Full backup in owner log group!</i>"
        )
        kb = [
            [InlineKeyboardButton("📂 MANAGE FILES", callback_data="files")],
            [InlineKeyboardButton("🔄 Refresh", callback_data="status")]
        ]
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

    elif data == "files":
        files = user_files.get(str(user_id), [])
        if not files:
            await q.edit_message_text("📂 <b>No files uploaded yet!</b>\n\n🔑 Pehle key daalo → .py upload karo", parse_mode="HTML")
            return
        kb = []
        for f in sorted(files):
            status = "🟢 LIVE" if f in running else "🔴 STOPPED"
            kb.append([InlineKeyboardButton(f"{status} {f}", callback_data=f"file|{f}")])
        kb.append([InlineKeyboardButton("◀️ Back", callback_data="status")])
        await q.edit_message_text(f"📂 <b><u>MANAGE FILES</u></b> <code>({len(files)})</code>\n\n🌈 Select bot:", reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

    elif data.startswith("file|"):
        _, fname = data.split("|", 1)
        is_running = fname in running
        status_text = "🟢 <b>RUNNING</b>" if is_running else "🔴 <b>STOPPED</b>"
        text = (
            "📄 <b><u>FILE DETAILS</u></b>\n\n"
            f"📄 <b>Name   :</b> <code>{fname}</code>\n"
            f"🐍 <b>Type   :</b> Python Script\n"
            f"📊 <b>Status :</b> {status_text}\n\n"
            "✨ Auto restart active\n"
            "⏰ 30-min logs in chat\n"
            "🛡️ Backup in owner GC"
        )
        kb = []
        if not is_running:
            kb.append([InlineKeyboardButton("🚀 START BOT", callback_data=f"start|{fname}")])
        else:
            kb += [[InlineKeyboardButton("🔄 RESTART BOT", callback_data=f"restart|{fname}")],
                   [InlineKeyboardButton("🛑 STOP BOT", callback_data=f"stop|{fname}")]]
        kb += [[InlineKeyboardButton("📜 VIEW LOGS", callback_data=f"logs|{fname}")],
               [InlineKeyboardButton("🗑️ DELETE FILE", callback_data=f"delete|{fname}")],
               [InlineKeyboardButton("◀️ Back", callback_data="files")]]
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

    elif data.startswith(("start|", "restart|")):
        _, fname = data.split("|", 1)
        await start_process(fname, context, user_chat_id, user_id)
        action = "RESTARTED" if data.startswith("restart") else "STARTED"
        await q.edit_message_text(
            f"🟢 <b>{fname}</b>\n✅ <i>{action} SUCCESSFULLY!</i>\n\n"
            "✨ Auto restart enabled\n📜 Logs har 30 min\n💚 Backup in owner GC",
            parse_mode="HTML"
        )
        await context.bot.send_message(LOG_GC_ID, f"▶️ <b>{fname}</b> {action} by <code>{user_id}</code>", parse_mode="HTML")

    elif data.startswith("stop|"):
        _, fname = data.split("|", 1)
        if fname in running:
            running[fname]["proc"].terminate()
            running[fname]["log_file"].close()
            running[fname]["monitor_task"].cancel()
            running[fname]["periodic_task"].cancel()
            del running[fname]
            await q.edit_message_text(f"🔴 <b>{fname}</b> STOPPED 🛑\n\nSafely terminated", parse_mode="HTML")
            await context.bot.send_message(LOG_GC_ID, f"⏹️ <b>{fname}</b> STOPPED by <code>{user_id}</code>", parse_mode="HTML")

    elif data.startswith("logs|"):
        _, fname = data.split("|", 1)
        log_path = f"{LOG_DIR}/{fname}.log"
        if os.path.exists(log_path) and os.path.getsize(log_path) > 0:
            with open(log_path, "r", errors="ignore") as f:
                logs = f.read()[-3800:]
            await q.message.reply_text(f"📜 <b>LOGS — {fname}</b>\n<pre>{logs}</pre>", parse_mode="HTML")
        else:
            await q.answer("No logs yet!", show_alert=True)

    elif data.startswith("delete|"):
        _, fname = data.split("|", 1)
        if fname in running:
            running[fname]["proc"].terminate()
            running[fname]["log_file"].close()
            del running[fname]
        for p in [os.path.join(UPLOAD_DIR, fname), f"{LOG_DIR}/{fname}.log"]:
            if os.path.exists(p): os.remove(p)
        remove_file_tracking(user_id, user_chat_id, fname)
        await q.edit_message_text(f"🗑️ <b>{fname}</b>\n✅ <i>Permanently DELETED</i>", parse_mode="HTML")
        await context.bot.send_message(LOG_GC_ID, f"🗑️ <b>{fname}</b> DELETED by <code>{user_id}</code>", parse_mode="HTML")

    elif data == "enterkey_prompt":
        await enterkey_prompt(update, context)

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

    async def error_handler(u, c):
        logging.error(traceback.format_exc())
        if OWNER_ID:
            await c.bot.send_message(OWNER_ID, f"⚠️ Bot Error:\n<pre>{traceback.format_exc()[-3000:]}</pre>", parse_mode="HTML")

    app.add_error_handler(error_handler)

    print("🚀💚 PRO COLORFUL MANAGER BOT LIVE HO GAYA! 💚🚀")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()