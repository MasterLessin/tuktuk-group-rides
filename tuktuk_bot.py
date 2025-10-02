"""
tuktuk_bot.py
Full TukTuk Group Rides bot (Postgres version).

Requirements:
- Python 3.10+
- python-telegram-bot==20.3
- psycopg2-binary

Environment variables (must be set):
- TELEGRAM_BOT_TOKEN    (Bot token from BotFather)
- ADMIN_ID              (your numeric Telegram user id)  <- REQUIRED
- DATABASE_URL          (Railway Postgres connection string)

This file:
- Connects to Postgres using DATABASE_URL
- Creates tables on startup: drivers, rides, settings
- Requires ADMIN_ID before starting
- Preserves rider & driver flows, dispatch posting, accept logic, location handling
"""

import os
import logging
import time
from typing import Optional

import psycopg2
from psycopg2.extras import RealDictCursor

from telegram import (
    Update, KeyboardButton, ReplyKeyboardMarkup,
    InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    ApplicationBuilder, ContextTypes,
    CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, filters
)

# ---------------------------
# Logging
# ---------------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# ---------------------------
# Environment variables (required)
# ---------------------------
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ADMIN_ID = os.environ.get("ADMIN_ID")
DATABASE_URL = os.environ.get("DATABASE_URL")

if not TOKEN:
    raise SystemExit("Error: set TELEGRAM_BOT_TOKEN environment variable first.")
if not ADMIN_ID:
    raise SystemExit("Error: set ADMIN_ID environment variable first.")
if not DATABASE_URL:
    raise SystemExit("Error: set DATABASE_URL environment variable first.")

try:
    ADMIN_ID = int(ADMIN_ID)
except Exception:
    raise SystemExit("Error: ADMIN_ID must be a numeric Telegram user id.")

# ---------------------------
# Conversation states
# ---------------------------
PICKUP, DROP, GROUP, CONFIRM = range(4)
DRV_NAME, DRV_REG, DRV_PHONE = range(10, 13)

# ---------------------------
# Postgres DB wrapper
# ---------------------------
class PostgresDB:
    def __init__(self, dsn):
        # We'll use sslmode=require for cloud Postgres (Railway)
        self.dsn = dsn
        self._connect()

    def _connect(self):
        self.conn = psycopg2.connect(self.dsn, sslmode="require", cursor_factory=RealDictCursor)
        self.cur = self.conn.cursor()

    def close(self):
        try:
            self.cur.close()
            self.conn.close()
        except Exception:
            pass

    def init_tables(self):
        # drivers table
        self.cur.execute("""
        CREATE TABLE IF NOT EXISTS drivers (
            id SERIAL PRIMARY KEY,
            telegram_id BIGINT UNIQUE,
            name TEXT,
            phone TEXT,
            reg_no TEXT,
            status TEXT,
            lat DOUBLE PRECISION,
            lng DOUBLE PRECISION,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)
        # rides table
        self.cur.execute("""
        CREATE TABLE IF NOT EXISTS rides (
            id SERIAL PRIMARY KEY,
            rider_tg_id BIGINT,
            pickup_lat DOUBLE PRECISION,
            pickup_lng DOUBLE PRECISION,
            drop_lat DOUBLE PRECISION,
            drop_lng DOUBLE PRECISION,
            group_size INTEGER,
            status TEXT,
            assigned_driver_id INTEGER REFERENCES drivers(id),
            created_at INTEGER
        );
        """)
        # settings table (key/value)
        self.cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            k TEXT PRIMARY KEY,
            v TEXT
        );
        """)
        self.conn.commit()

    # settings helpers
    def set_setting(self, k: str, v: str):
        self.cur.execute("""
            INSERT INTO settings (k, v) VALUES (%s, %s)
            ON CONFLICT (k) DO UPDATE SET v = EXCLUDED.v;
        """, (k, str(v)))
        self.conn.commit()

    def get_setting(self, k: str) -> Optional[str]:
        self.cur.execute("SELECT v FROM settings WHERE k = %s;", (k,))
        row = self.cur.fetchone()
        return row['v'] if row else None

    # driver helpers
    def add_or_update_driver(self, tg_id, name=None, phone=None, reg_no=None):
        # uses upsert on telegram_id
        self.cur.execute("""
            INSERT INTO drivers (telegram_id, name, phone, reg_no, status)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (telegram_id) DO UPDATE
              SET name = COALESCE(EXCLUDED.name, drivers.name),
                  phone = COALESCE(EXCLUDED.phone, drivers.phone),
                  reg_no = COALESCE(EXCLUDED.reg_no, drivers.reg_no);
        """, (tg_id, name, phone, reg_no, "offline"))
        self.conn.commit()

    def set_driver_status(self, tg_id, status):
        self.cur.execute("UPDATE drivers SET status = %s WHERE telegram_id = %s;", (status, tg_id))
        self.conn.commit()

    def update_driver_location(self, tg_id, lat, lng):
        self.cur.execute("UPDATE drivers SET lat = %s, lng = %s WHERE telegram_id = %s;", (lat, lng, tg_id))
        self.conn.commit()

    def get_driver_by_tg(self, tg_id):
        self.cur.execute("SELECT id, telegram_id, name, phone, reg_no, status, lat, lng FROM drivers WHERE telegram_id = %s;", (tg_id,))
        return self.cur.fetchone()

    def get_driver_by_id(self, driver_id):
        if not driver_id:
            return None
        self.cur.execute("SELECT id, telegram_id, name, phone, reg_no FROM drivers WHERE id = %s;", (driver_id,))
        return self.cur.fetchone()

    def get_online_drivers(self):
        self.cur.execute("SELECT id, telegram_id, name, phone, reg_no, status, lat, lng FROM drivers WHERE status = 'online';")
        return self.cur.fetchall()

    # rides
    def create_ride(self, rider_tg_id, pickup_lat, pickup_lng, drop_lat, drop_lng, group_size):
        ts = int(time.time())
        self.cur.execute("""
            INSERT INTO rides (rider_tg_id, pickup_lat, pickup_lng, drop_lat, drop_lng, group_size, status, created_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id;
        """, (rider_tg_id, pickup_lat, pickup_lng, drop_lat, drop_lng, group_size, "searching", ts))
        row = self.cur.fetchone()
        self.conn.commit()
        return row['id'] if row else None

    def get_ride(self, ride_id):
        self.cur.execute("SELECT * FROM rides WHERE id = %s;", (ride_id,))
        return self.cur.fetchone()

    def get_rides_by_rider(self, rider_tg_id, limit=20):
        self.cur.execute("""
            SELECT id, pickup_lat, pickup_lng, drop_lat, drop_lng, group_size, status, assigned_driver_id, created_at
            FROM rides WHERE rider_tg_id = %s ORDER BY created_at DESC LIMIT %s;
        """, (rider_tg_id, limit))
        return self.cur.fetchall()

    def assign_ride_if_unassigned(self, ride_id, driver_id):
        self.cur.execute("""
            UPDATE rides SET assigned_driver_id = %s, status = 'assigned' 
            WHERE id = %s AND assigned_driver_id IS NULL;
        """, (driver_id, ride_id))
        self.conn.commit()
        return self.cur.rowcount > 0

    def set_ride_status(self, ride_id, status):
        self.cur.execute("UPDATE rides SET status = %s WHERE id = %s;", (status, ride_id))
        self.conn.commit()

# instantiate DB
db = PostgresDB(DATABASE_URL)
db.init_tables()

# ---------- Handlers ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = ReplyKeyboardMarkup([["Request Group Ride", "My Rides", "Help"]], resize_keyboard=True)
    await update.message.reply_text("Welcome to TukTuk Group Rides! Use the buttons below to start.", reply_markup=kb)

# ----- Rider Conversation flow -----
async def request_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = ReplyKeyboardMarkup([[KeyboardButton("Share Location", request_location=True)]], one_time_keyboard=True, resize_keyboard=True)
    await update.message.reply_text("Please share your pickup location (press the button):", reply_markup=kb)
    return PICKUP

async def pickup_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.location:
        await update.message.reply_text("Please use the *Share Location* button so we get accurate coordinates.")
        return PICKUP
    loc = update.message.location
    context.user_data['pickup_lat'] = loc.latitude
    context.user_data['pickup_lng'] = loc.longitude
    kb = ReplyKeyboardMarkup([[KeyboardButton("Share Drop-off Location", request_location=True)], ["Skip"]], one_time_keyboard=True, resize_keyboard=True)
    await update.message.reply_text("Got pickup. Share drop-off location or press Skip.", reply_markup=kb)
    return DROP

async def drop_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # If user shared location, use that. If typed "Skip" or sent text, handle accordingly.
    if update.message.location:
        loc = update.message.location
        context.user_data['drop_lat'] = loc.latitude
        context.user_data['drop_lng'] = loc.longitude
    else:
        # typed Skip or typed address
        if update.message.text and update.message.text.strip().lower() == "skip":
            context.user_data['drop_lat'] = None
            context.user_data['drop_lng'] = None
        else:
            # user typed text address - we cannot geocode here; we will store as None and include the text as "drop_text"
            context.user_data['drop_lat'] = None
            context.user_data['drop_lng'] = None
            context.user_data['drop_text'] = update.message.text.strip()
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

    p_lat = context.user_data.get('pickup_lat')
    p_lng = context.user_data.get('pickup_lng')
    d_lat = context.user_data.get('drop_lat')
    d_lng = context.user_data.get('drop_lng')
    d_text = context.user_data.get('drop_text')
    group = context.user_data['group_size']

    summary = f"Please confirm your request:\nPickup: ({p_lat:.5f}, {p_lng:.5f})\n"
    if d_lat:
        summary += f"Drop: ({d_lat:.5f}, {d_lng:.5f})\n"
    elif d_text:
        summary += f"Drop (text): {d_text}\n"
    else:
        summary += "Drop: Not provided\n"
    summary += f"Group size: {group}\nPayment: Cash"

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
        pickup_lat=context.user_data.get('pickup_lat'),
        pickup_lng=context.user_data.get('pickup_lng'),
        drop_lat=context.user_data.get('drop_lat'),
        drop_lng=context.user_data.get('drop_lng'),
        group_size=context.user_data.get('group_size')
    )

    await query.edit_message_text("Searching for a driver nearby... ✅")

    # post to dispatch group if set
    dispatch_chat = db.get_setting("dispatch_chat_id")
    if not dispatch_chat:
        await query.message.reply_text(
            "Dispatch group not set. Please ask the admin to run /set_dispatch_group in the driver group."
        )
        return ConversationHandler.END

    # Build dispatch message text
    p_lat = context.user_data.get('pickup_lat')
    p_lng = context.user_data.get('pickup_lng')
    group = context.user_data.get('group_size')
    rider_name = query.from_user.first_name or ""
    rider_tg_id = rider_id

    dispatch_text = (
        f"🚖 New Ride Request (ID:{ride_id})\n"
        f"Rider: {rider_name} (tg:{rider_tg_id})\n"
        f"Pickup: ({p_lat:.5f}, {p_lng:.5f})\n"
        f"Group size: {group}\nPayment: Cash"
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Accept", callback_data=f"accept:{ride_id}")]])
    try:
        await context.bot.send_message(chat_id=int(dispatch_chat), text=dispatch_text, reply_markup=kb)
        await context.bot.send_message(chat_id=rider_tg_id, text="Request posted to drivers. We'll notify you when someone accepts.")
    except Exception as e:
        logger.warning("Failed to post to dispatch group or notify rider: %s", e)
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

    # check that user is a registered driver
    drv = db.get_driver_by_tg(user.id)
    if not drv:
        await query.edit_message_text("Only registered drivers can accept rides. Please register with /driver_start.")
        return

    driver_db_id = drv['id']

    # attempt to assign
    assigned = db.assign_ride_if_unassigned(ride_id, driver_db_id)
    if not assigned:
        await query.edit_message_text("Sorry — this ride was already taken by another driver.")
        return

    # success: inform driver & rider
    ride = db.get_ride(ride_id)
    rider_tg_id = ride['rider_tg_id']

    assigned_text = f"✅ Ride {ride_id} assigned to driver {drv['name']} (tel: {drv['phone']})."
    await query.edit_message_text(assigned_text)

    # Notify rider
    try:
        await context.bot.send_message(chat_id=rider_tg_id,
                                       text=f"Driver assigned ✅\nName: {drv['name']}\nPhone: {drv['phone']}\nVehicle: {drv['reg_no']}\nPlease wait for the driver to arrive.")
    except Exception as e:
        logger.warning("Failed to notify rider: %s", e)

    # Notify driver (DM)
    try:
        await context.bot.send_message(chat_id=user.id,
                                       text=f"You accepted ride {ride_id}.\nPickup coords: ({ride['pickup_lat']:.5f}, {ride['pickup_lng']:.5f})\nUse /complete_ride {ride_id} when done.")
    except Exception as e:
        logger.warning("Failed to DM driver: %s", e)

# ---------- Admin: set dispatch group ----------
async def set_dispatch_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # must be run inside the group that will serve as dispatch group
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Only the admin can set the dispatch group.")
        return

    chat_id = update.effective_chat.id
    db.set_setting("dispatch_chat_id", str(chat_id))
    await update.message.reply_text(f"Dispatch group saved (chat_id: {chat_id}). Drivers will receive ride requests here.")

# ---------- Driver registration flow ----------
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

# driver commands
async def go_online(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = update.effective_user.id
    drv = db.get_driver_by_tg(tg_id)
    if not drv:
        await update.message.reply_text("You're not registered. Run /driver_start to register first.")
        return
    db.set_driver_status(tg_id, "online")
    kb = ReplyKeyboardMarkup([[KeyboardButton("Share Location", request_location=True)]], one_time_keyboard=True, resize_keyboard=True)
    await update.message.reply_text("You're now ONLINE. Please share your current location so the system can find you for nearby rides.", reply_markup=kb)

# capture location updates
async def location_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not update.message.location:
        await update.message.reply_text("Please use the location sharing button.")
        return
    loc = update.message.location
    # if the user is a driver, update location
    drv = db.get_driver_by_tg(user.id)
    if drv:
        db.update_driver_location(user.id, loc.latitude, loc.longitude)
        await update.message.reply_text("Location updated.")
    else:
        await update.message.reply_text("Location received.")

# driver completes ride
async def complete_ride(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /complete_ride <ride_id>")
        return
    try:
        ride_id = int(args[0])
    except ValueError:
        await update.message.reply_text("Invalid ride id.")
        return
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
        ride_id = r['id']
        p_lat = r['pickup_lat']
        p_lng = r['pickup_lng']
        d_lat = r['drop_lat']
        d_lng = r['drop_lng']
        group_size = r['group_size']
        status = r['status']
        assigned_driver_id = r['assigned_driver_id']
        created_at = r['created_at']
        created = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(created_at)) if created_at else ""
        line = f"Ride ID: {ride_id}\nStatus: {status}\nGroup: {group_size}\nPickup: ({p_lat:.5f}, {p_lng:.5f})"
        if d_lat:
            line += f"\nDrop: ({d_lat:.5f}, {d_lng:.5f})"
        if assigned_driver_id:
            drv = db.get_driver_by_id(assigned_driver_id)
            if drv:
                line += f"\nDriver: {drv['name']} | {drv['phone']} | {drv['reg_no']}"
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
        "/set_dispatch_group — (admin) set the dispatch group where drivers receive requests\n"
    )
    await update.message.reply_text(txt)

# ---------- Main ----------
def main():
    app = ApplicationBuilder().token(TOKEN).build()

    # basic handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("set_dispatch_group", set_dispatch_group))
    app.add_handler(CommandHandler("help", help_command))

    # rider conversation (supports keyboard entry or command)
    ride_conv = ConversationHandler(
        entry_points=[CommandHandler("request", request_start), MessageHandler(filters.Regex(r"^Request Group Ride$"), request_start)],
        states={
            PICKUP: [MessageHandler(filters.LOCATION, pickup_received)],
            DROP: [MessageHandler(filters.LOCATION | filters.Regex("^Skip$"), drop_received)],
            GROUP: [CallbackQueryHandler(group_callback, pattern="^group:")],
            CONFIRM: [CallbackQueryHandler(confirm_callback, pattern="^confirm:")]
        },
        fallbacks=[CommandHandler("cancel", cancel_conv)]
    )
    app.add_handler(ride_conv)

    # driver registration conversation
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

    # keyboard button handlers
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
