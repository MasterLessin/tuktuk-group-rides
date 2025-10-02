"""
tuktuk_bot.py
Cash-only MVP Telegram bot for TukTuk group rides (starter).

Requirements:
- Python 3.10+
- python-telegram-bot==20.3

For deployment (Railway/Heroku): set environment variables
TELEGRAM_BOT_TOKEN and ADMIN_ID in the host dashboard.
"""

import os
import logging
import sqlite3
import time
from typing import Optional
from datetime import datetime

from telegram import (
    Update, KeyboardButton, ReplyKeyboardMarkup,
    InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    ApplicationBuilder, ContextTypes,
    CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, filters
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Conversation states
PICKUP, DROP, GROUP, CONFIRM = range(4)
DRV_NAME, DRV_REG, DRV_PHONE = range(10, 13)

DB_PATH = "tuk.db"


class DB:
    def __init__(self, path=DB_PATH):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.cur = self.conn.cursor()
        self.create_tables()

    def create_tables(self):
        self.cur.execute("""CREATE TABLE IF NOT EXISTS drivers (
            id INTEGER PRIMARY KEY,
            telegram_id INTEGER UNIQUE,
            name TEXT,
            phone TEXT,
            reg_no TEXT,
            status TEXT,
            lat REAL,
            lng REAL
        )""")
        self.cur.execute("""CREATE TABLE IF NOT EXISTS rides (
            id INTEGER PRIMARY KEY,
            rider_tg_id INTEGER,
            pickup_lat REAL,
            pickup_lng REAL,
            drop_lat REAL,
            drop_lng REAL,
            group_size INTEGER,
            status TEXT,
            assigned_driver_id INTEGER,
            created_at INTEGER
        )""")
        self.cur.execute("""CREATE TABLE IF NOT EXISTS settings (
            k TEXT PRIMARY KEY,
            v TEXT
        )""")
        self.conn.commit()

    # settings helpers
    def set_setting(self, k, v):
        self.cur.execute("INSERT OR REPLACE INTO settings (k,v) VALUES (?,?)", (k, str(v)))
        self.conn.commit()

    def get_setting(self, k) -> Optional[str]:
        self.cur.execute("SELECT v FROM settings WHERE k=?", (k,))
        r = self.cur.fetchone()
        return r[0] if r else None

    # driver helpers
    def add_or_update_driver(self, tg_id, name=None, phone=None, reg_no=None):
        self.cur.execute("SELECT id FROM drivers WHERE telegram_id=?", (tg_id,))
        if self.cur.fetchone():
            self.cur.execute("""
                UPDATE drivers SET name=COALESCE(?,name), phone=COALESCE(?,phone), reg_no=COALESCE(?,reg_no)
                WHERE telegram_id=?
            """, (name, phone, reg_no, tg_id))
        else:
            self.cur.execute("INSERT INTO drivers (telegram_id, name, phone, reg_no, status) VALUES (?,?,?,?,?)",
                             (tg_id, name, phone, reg_no, "offline"))
        self.conn.commit()

    def set_driver_status(self, tg_id, status):
        self.cur.execute("UPDATE drivers SET status=? WHERE telegram_id=?", (status, tg_id))
        self.conn.commit()

    def update_driver_location(self, tg_id, lat, lng):
        self.cur.execute("UPDATE drivers SET lat=?, lng=? WHERE telegram_id=?", (lat, lng, tg_id))
        self.conn.commit()

    def get_driver_by_tg(self, tg_id):
        self.cur.execute("SELECT id, telegram_id, name, phone, reg_no, status, lat, lng FROM drivers WHERE telegram_id=?", (tg_id,))
        return self.cur.fetchone()

    def get_driver_by_id(self, driver_id):
        if not driver_id:
            return None
        self.cur.execute("SELECT id, telegram_id, name, phone, reg_no FROM drivers WHERE id=?", (driver_id,))
        return self.cur.fetchone()

    def get_online_drivers(self):
        self.cur.execute("SELECT id, telegram_id, name, phone, reg_no, status, lat, lng FROM drivers WHERE status='online'")
        return self.cur.fetchall()

    # ride helpers
    def create_ride(self, rider_tg_id, pickup_lat, pickup_lng, drop_lat, drop_lng, group_size):
        ts = int(time.time())
        self.cur.execute("""
            INSERT INTO rides (rider_tg_id, pickup_lat, pickup_lng, drop_lat, drop_lng, group_size, status, created_at)
            VALUES (?,?,?,?,?,?,?,?)
        """, (rider_tg_id, pickup_lat, pickup_lng, drop_lat, drop_lng, group_size, "searching", ts))
        self.conn.commit()
        return self.cur.lastrowid

    def get_ride(self, ride_id):
        self.cur.execute("SELECT * FROM rides WHERE id=?", (ride_id,))
        return self.cur.fetchone()

    def get_rides_by_rider(self, rider_tg_id, limit=20):
        self.cur.execute("""
            SELECT id, pickup_lat, pickup_lng, drop_lat, drop_lng, group_size, status, assigned_driver_id, created_at
            FROM rides WHERE rider_tg_id=? ORDER BY created_at DESC LIMIT ?
        """, (rider_tg_id, limit))
        return self.cur.fetchall()

    def assign_ride_if_unassigned(self, ride_id, driver_id):
        self.cur.execute("""
            UPDATE rides SET assigned_driver_id=?, status='assigned' 
            WHERE id=? AND assigned_driver_id IS NULL
        """, (driver_id, ride_id))
        self.conn.commit()
        return self.cur.rowcount > 0

    def set_ride_status(self, ride_id, status):
        self.cur.execute("UPDATE rides SET status=? WHERE id=?", (status, ride_id))
        self.conn.commit()


db = DB()


# ---------- Handlers ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = ReplyKeyboardMarkup([["Request Group Ride", "My Rides", "Help"]], resize_keyboard=True)
    await update.message.reply_text("Welcome to TukTuk Group Rides! Use the buttons below to start.", reply_markup=kb)


# ---------- Rider flow ----------
async def request_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = ReplyKeyboardMarkup([[KeyboardButton("Share Location", request_location=True)]], one_time_keyboard=True, resize_keyboard=True)
    await update.message.reply_text("Please share your pickup location (press the button):", reply_markup=kb)
    return PICKUP


async def pickup_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    loc = update.message.location
    context.user_data['pickup_lat'] = loc.latitude
    context.user_data['pickup_lng'] = loc.longitude
    kb = ReplyKeyboardMarkup([[KeyboardButton("Share Drop-off Location", request_location=True)], ["Skip"]], one_time_keyboard=True, resize_keyboard=True)
    await update.message.reply_text("Got pickup. Share drop-off location or press Skip.", reply_markup=kb)
    return DROP


async def drop_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.location:
        loc = update.message.location
        context.user_data['drop_lat'] = loc.latitude
        context.user_data['drop_lng'] = loc.longitude
    else:
        context.user_data['drop_lat'] = None
        context.user_data['drop_lng'] = None

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("1-2", callback_data="group:1"),
         InlineKeyboardButton("3-4", callback_data="group:3"),
         InlineKeyboardButton("5+", callback_data="group:5")]
    ])
    await update.message.reply_text("How many people in your group?", reply_markup=kb)
    return GROUP


async def group_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, num = query.data.split(":")
    context.user_data['group_size'] = int(num)

    summary = f"Please confirm your request:\nPickup: ({context.user_data['pickup_lat']:.5f}, {context.user_data['pickup_lng']:.5f})\n"
    if context.user_data.get('drop_lat'):
        summary += f"Drop: ({context.user_data['drop_lat']:.5f}, {context.user_data['drop_lng']:.5f})\n"
    else:
        summary += "Drop: Not provided\n"
    summary += f"Group size: {context.user_data['group_size']}\nPayment: Cash"

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Confirm", callback_data="confirm:yes"),
         InlineKeyboardButton("❌ Cancel", callback_data="confirm:no")]
    ])
    await query.edit_message_text(summary, reply_markup=kb)
    return CONFIRM


async def confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "confirm:no":
        await query.edit_message_text("Request cancelled.")
        return ConversationHandler.END

    rider_id = query.from_user.id
    ride_id = db.create_ride(
        rider_tg_id=rider_id,
        pickup_lat=context.user_data['pickup_lat'],
        pickup_lng=context.user_data['pickup_lng'],
        drop_lat=context.user_data.get('drop_lat'),
        drop_lng=context.user_data.get('drop_lng'),
        group_size=context.user_data['group_size']
    )

    await query.edit_message_text("Searching for a driver nearby... ✅")
    dispatch_chat = db.get_setting("dispatch_chat_id")
    if not dispatch_chat:
        await query.message.reply_text("Dispatch group not set. Please ask the admin to run /set_dispatch_group in the driver group.")
        return ConversationHandler.END

    dispatch_text = (
        f"🚖 New Ride Request (ID:{ride_id})\n"
        f"Pickup: ({context.user_data['pickup_lat']:.5f}, {context.user_data['pickup_lng']:.5f})\n"
        f"Group size: {context.user_data['group_size']}\n"
        f"Payment: Cash\n"
        f"Rider Telegram ID: {rider_id}"
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Accept", callback_data=f"accept:{ride_id}")]])
    await context.bot.send_message(chat_id=int(dispatch_chat), text=dispatch_text, reply_markup=kb)
    await context.bot.send_message(chat_id=rider_id, text="Request posted to drivers. We'll notify you when someone accepts.")
    return ConversationHandler.END


async def cancel_conv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Request cancelled.")
    return ConversationHandler.END


# ---------- Dispatch accept callback ----------
async def accept_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, ride_id_s = query.data.split(":")
    ride_id = int(ride_id_s)
    user = query.from_user

    drv = db.get_driver_by_tg(user.id)
    if not drv:
        await query.edit_message_text("Only registered drivers can accept rides. Please register with /driver_start.")
        return

    driver_db_id = drv[0]
    assigned = db.assign_ride_if_unassigned(ride_id, driver_db_id)
    if not assigned:
        await query.edit_message_text("Sorry — this ride was already taken by another driver.")
        return

    ride = db.get_ride(ride_id)
    rider_tg_id = ride[1]
    assigned_text = f"✅ Ride {ride_id} assigned to driver {drv[2]} (tel: {drv[3]})."
    await query.edit_message_text(assigned_text)

    try:
        await context.bot.send_message(chat_id=rider_tg_id,
                                       text=f"Driver assigned ✅\nName: {drv[2]}\nPhone: {drv[3]}\nVehicle: {drv[4]}\nPlease wait for the driver.")
    except Exception as e:
        logger.warning("Failed to notify rider: %s", e)

    try:
        await context.bot.send_message(chat_id=user.id,
                                       text=f"You accepted ride {ride_id}.\nPickup coords: ({ride[2]:.5f}, {ride[3]:.5f})\nUse /complete_ride {ride_id} when done.")
    except Exception as e:
        logger.warning("Failed to DM driver: %s", e)


# ---------- Admin ----------
async def set_dispatch_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = os.environ.get("ADMIN_ID")
    if not admin_id:
        await update.message.reply_text("ADMIN_ID not configured. Set ADMIN_ID before starting bot.")
        return

    if str(update.effective_user.id) != str(admin_id):
        await update.message.reply_text("Only the admin can set the dispatch group.")
        return

    chat_id = update.effective_chat.id
    db.set_setting("dispatch_chat_id", chat_id)
    await update.message.reply_text(f"Dispatch group saved (chat_id: {chat_id}). Drivers will receive ride requests here.")


# ---------- Driver registration ----------
async def driver_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Driver registration — what's your full name?")
    return DRV_NAME


async def driver_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['drv_name'] = update.message.text.strip()
    await update.message.reply_text("Vehicle registration number (e.g., KBA 123A)?")
    return DRV_REG


async def driver_reg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['drv_reg'] = update.message.text.strip()
    await update.message.reply_text("Phone number (07xx...):")
    return DRV_PHONE


async def driver_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    tg_id = update.effective_user.id
    db.add_or_update_driver(tg_id, name=context.user_data['drv_name'], phone=phone, reg_no=context.user_data['drv_reg'])
    await update.message.reply_text("Thanks — you're registered. Use /go_online to set yourself online and share location.")
    return ConversationHandler.END


async def go_online(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = update.effective_user.id
    drv = db.get_driver_by_tg(tg_id)
    if not drv:
        await update.message.reply_text("You're not registered. Run /driver_start first.")
        return
    db.set_driver_status(tg_id, "online")
    kb = ReplyKeyboardMarkup([[KeyboardButton("Share Location", request_location=True)]], one_time_keyboard=True, resize_keyboard=True)
    await update.message.reply_text("You're now ONLINE. Share your current location.", reply_markup=kb)


async def location_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    loc = update.message.location
    drv = db.get_driver_by_tg(user.id)
    if drv:
        db.update_driver_location(user.id, loc.latitude, loc.longitude)
        await update.message.reply_text("Location updated.")
    else:
        await update.message.reply_text("Location received.")


async def complete_ride(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /complete_ride <ride_id>")
        return
    ride_id = int(args[0])
    db.set_ride_status(ride_id, "completed")
    await update.message.reply_text(f"Ride {ride_id} marked as completed. Thanks!")


# ---------- My Rides & Help handlers ----------
async def my_rides(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rider_id = update.effective_user.id
    rides = db.get_rides_by_rider(rider_id)
    if not rides:
        await update.message.reply_text("You have no rides yet.")
        return

    lines = []
    for r in rides:
        # r: id, pickup_lat, pickup_lng, drop_lat, drop_lng, group_size, status, assigned_driver_id, created_at
        ride_id, p_lat, p_lng, d_lat, d_lng, group_size, status, assigned_driver_id, created_at = r
        created = datetime.fromtimestamp(created_at).strftime("%Y-%m-%d %H:%M:%S")
        line = f"Ride ID: {ride_id}\nStatus: {status}\nGroup: {group_size}\nPickup: ({p_lat:.5f}, {p_lng:.5f})"
        if d_lat:
            line += f"\nDrop: ({d_lat:.5f}, {d_lng:.5f})"
        if assigned_driver_id:
            drv = db.get_driver_by_id(assigned_driver_id)
            if drv:
                line += f"\nDriver: {drv[2]} | {drv[3]} | {drv[4]}"
            else:
                line += f"\nDriver ID: {assigned_driver_id}"
        line += f"\nCreated: {created}"
        lines.append(line)

    msg = "\n\n".join(lines)
    await update.message.reply_text(msg)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (
        "TukTuk Bot Help\n\n"
        "Use the buttons:\n"
        "• Request Group Ride — start a new group ride (share location, choose group size, confirm).\n"
        "• My Rides — see your recent rides and statuses.\n"
        "• Help — show this message.\n\n"
        "Commands:\n"
        "/driver_start — register as a driver\n"
        "/go_online — set yourself online (drivers)\n"
    )
    await update.message.reply_text(txt)


# ---------- main ----------
def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("Error: set TELEGRAM_BOT_TOKEN environment variable first.")
        return

    app = ApplicationBuilder().token(token).build()

    # basic handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("set_dispatch_group", set_dispatch_group))
    app.add_handler(CommandHandler("help", help_command))

    # conversation: add MessageHandler as an entry point so keyboard button works
    ride_conv = ConversationHandler(
        entry_points=[
            CommandHandler("request", request_start),
            MessageHandler(filters.Regex(r"^Request Group Ride$"), request_start)
        ],
        states={
            PICKUP: [MessageHandler(filters.LOCATION, pickup_received)],
            DROP: [MessageHandler(filters.LOCATION | filters.Regex("^Skip$"), drop_received)],
            GROUP: [CallbackQueryHandler(group_callback, pattern="^group:")],
            CONFIRM: [CallbackQueryHandler(confirm_callback, pattern="^confirm:")]
        },
        fallbacks=[CommandHandler("cancel", cancel_conv)]
    )
    app.add_handler(ride_conv)

    # driver registration
    drv_conv = ConversationHandler(
        entry_points=[CommandHandler("driver_start", driver_start)],
        states={
            DRV_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, driver_name)],
            DRV_REG: [MessageHandler(filters.TEXT & ~filters.COMMAND, driver_reg)],
            DRV_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, driver_phone)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conv)]
    )
    app.add_handler(drv_conv)

    # button handlers for keyboard
    app.add_handler(MessageHandler(filters.Regex(r"^My Rides$"), my_rides))
    app.add_handler(MessageHandler(filters.Regex(r"^Help$"), help_command))

    # driver actions & callbacks
    app.add_handler(CommandHandler("go_online", go_online))
    app.add_handler(MessageHandler(filters.LOCATION, location_handler))
    app.add_handler(CallbackQueryHandler(accept_callback, pattern="^accept:"))
    app.add_handler(CommandHandler("complete_ride", complete_ride))

    print("Bot started (polling). Press Ctrl+C to stop.")
    app.run_polling()


if __name__ == "__main__":
    main()
