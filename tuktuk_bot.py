#!/usr/bin/env python3
"""
tuktuk_bot.py
Full TukTuk Group Rides bot (Postgres-backed, async).

Features:
- Rider flow: Request Group Ride (share pickup location, optional drop, choose group size, confirm)
- Driver flow: /driver_start -> register, /go_online -> set online and share location
- Dispatch: admin sets dispatch group once with /set_dispatch_group; ride requests automatically posted there
- Drivers accept using inline button; atomic assignment in DB prevents double-assign
- My Rides: riders can list their recent rides
- Complete Ride: drivers mark rides complete
- All data stored in Postgres (DATABASE_URL)
- DB tables auto-created at startup (Option B)
- ADMIN_ID required (bot refuses to start without it)

ENV variables required:
- TELEGRAM_BOT_TOKEN
- ADMIN_ID (numeric)
- DATABASE_URL

Dependencies:
- python-telegram-bot==20.3
- asyncpg
- APScheduler (optional if you will use scheduling)
"""

import os
import sys
import logging
import asyncio
import time
from typing import Optional, Dict, Any, List

from telegram import (
    Update,
    KeyboardButton,
    ReplyKeyboardMarkup,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
)

import asyncpg

# -------------------------
# Configuration & Logging
# -------------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Conversation states
PICKUP, DROP, GROUP, CONFIRM = range(4)
DRV_NAME, DRV_REG, DRV_PHONE = range(10, 13)

# Read environment
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ADMIN_ID_ENV = os.environ.get("ADMIN_ID")
DATABASE_URL = os.environ.get("DATABASE_URL")

if not TOKEN:
    logger.error("TELEGRAM_BOT_TOKEN is not set in the environment. Exiting.")
    sys.exit(1)
if not ADMIN_ID_ENV:
    logger.error("ADMIN_ID is not set in the environment. Exiting.")
    sys.exit(1)
if not DATABASE_URL:
    logger.error("DATABASE_URL is not set in the environment. Exiting.")
    sys.exit(1)

try:
    ADMIN_ID = int(ADMIN_ID_ENV)
except Exception as e:
    logger.error("ADMIN_ID must be a numeric Telegram user id. Error: %s", e)
    sys.exit(1)

# -------------------------
# Async Postgres DB wrapper
# -------------------------
class AsyncDB:
    """
    Async wrapper around asyncpg to hold a connection pool and helpers.
    """

    def __init__(self, database_url: str):
        self.database_url = database_url
        self.pool: Optional[asyncpg.Pool] = None

    async def init(self):
        logger.info("Creating asyncpg pool...")
        # create pool with min_size=1, max_size small (fits bot)
        self.pool = await asyncpg.create_pool(dsn=self.database_url, min_size=1, max_size=5)
        logger.info("Pool created. Creating tables if missing...")
        await self._create_tables()
        logger.info("Database ready.")

    async def close(self):
        if self.pool:
            await self.pool.close()
            self.pool = None
            logger.info("Database pool closed.")

    async def _create_tables(self):
        """
        Create the required tables: drivers, rides, settings (key/value).
        """
        create_drivers = """
        CREATE TABLE IF NOT EXISTS drivers (
            id SERIAL PRIMARY KEY,
            telegram_id BIGINT UNIQUE,
            name TEXT,
            phone TEXT,
            reg_no TEXT,
            status TEXT,
            lat DOUBLE PRECISION,
            lng DOUBLE PRECISION,
            created_at TIMESTAMP DEFAULT NOW()
        );
        """
        create_rides = """
        CREATE TABLE IF NOT EXISTS rides (
            id SERIAL PRIMARY KEY,
            rider_tg_id BIGINT,
            pickup_lat DOUBLE PRECISION,
            pickup_lng DOUBLE PRECISION,
            drop_lat DOUBLE PRECISION,
            drop_lng DOUBLE PRECISION,
            drop_text TEXT,
            group_size INTEGER,
            status TEXT,
            assigned_driver_id INTEGER REFERENCES drivers(id),
            created_at BIGINT
        );
        """
        create_settings = """
        CREATE TABLE IF NOT EXISTS settings (
            k TEXT PRIMARY KEY,
            v TEXT
        );
        """
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(create_drivers)
                await conn.execute(create_rides)
                await conn.execute(create_settings)

    # ---------- settings helpers ----------
    async def set_setting(self, k: str, v: str):
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO settings (k, v) VALUES ($1, $2)
                ON CONFLICT (k) DO UPDATE SET v = EXCLUDED.v;
                """,
                k,
                v,
            )

    async def get_setting(self, k: str) -> Optional[str]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT v FROM settings WHERE k=$1;", k)
            return row["v"] if row else None

    # ---------- driver helpers ----------
    async def add_or_update_driver(self, tg_id: int, name: Optional[str] = None, phone: Optional[str] = None, reg_no: Optional[str] = None):
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO drivers (telegram_id, name, phone, reg_no, status)
                VALUES ($1, $2, $3, $4, 'offline')
                ON CONFLICT (telegram_id) DO UPDATE
                  SET name = COALESCE(EXCLUDED.name, drivers.name),
                      phone = COALESCE(EXCLUDED.phone, drivers.phone),
                      reg_no = COALESCE(EXCLUDED.reg_no, drivers.reg_no);
                """,
                tg_id,
                name,
                phone,
                reg_no,
            )

    async def set_driver_status(self, tg_id: int, status: str):
        async with self.pool.acquire() as conn:
            await conn.execute("UPDATE drivers SET status=$1 WHERE telegram_id=$2;", status, tg_id)

    async def update_driver_location(self, tg_id: int, lat: float, lng: float):
        async with self.pool.acquire() as conn:
            await conn.execute("UPDATE drivers SET lat=$1, lng=$2 WHERE telegram_id=$3;", lat, lng, tg_id)

    async def get_driver_by_tg(self, tg_id: int) -> Optional[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM drivers WHERE telegram_id=$1;", tg_id)
            return dict(row) if row else None

    async def get_driver_by_id(self, driver_id: int) -> Optional[Dict[str, Any]]:
        if not driver_id:
            return None
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM drivers WHERE id=$1;", driver_id)
            return dict(row) if row else None

    async def get_online_drivers(self) -> List[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM drivers WHERE status='online';")
            return [dict(r) for r in rows]

    # ---------- ride helpers ----------
    async def create_ride(self, rider_tg_id: int, pickup_lat: float, pickup_lng: float,
                          drop_lat: Optional[float], drop_lng: Optional[float],
                          drop_text: Optional[str], group_size: int) -> int:
        ts = int(time.time())
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO rides (rider_tg_id, pickup_lat, pickup_lng, drop_lat, drop_lng, drop_text, group_size, status, created_at)
                VALUES ($1,$2,$3,$4,$5,$6,$7,'searching',$8) RETURNING id;
                """,
                rider_tg_id, pickup_lat, pickup_lng, drop_lat, drop_lng, drop_text, group_size, ts
            )
            return int(row["id"])

    async def get_ride(self, ride_id: int) -> Optional[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM rides WHERE id=$1;", ride_id)
            return dict(row) if row else None

    async def get_rides_by_rider(self, rider_tg_id: int, limit: int = 20) -> List[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, pickup_lat, pickup_lng, drop_lat, drop_lng, drop_text, group_size, status, assigned_driver_id, created_at
                FROM rides WHERE rider_tg_id=$1 ORDER BY created_at DESC LIMIT $2;
                """,
                rider_tg_id, limit
            )
            return [dict(r) for r in rows]

    async def assign_ride_if_unassigned(self, ride_id: int, driver_id: int) -> bool:
        """
        Atomic assignment: update only if assigned_driver_id IS NULL.
        Returns True if we successfully updated.
        """
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE rides SET assigned_driver_id=$1, status='assigned'
                WHERE id=$2 AND assigned_driver_id IS NULL;
                """,
                driver_id, ride_id
            )
            # result is like 'UPDATE <n>'
            updated = result.split()[-1]
            try:
                return int(updated) > 0
            except Exception:
                return False

    async def set_ride_status(self, ride_id: int, status: str):
        async with self.pool.acquire() as conn:
            await conn.execute("UPDATE rides SET status=$1 WHERE id=$2;", status, ride_id)


# instantiate global db
db = AsyncDB(DATABASE_URL)

# -------------------------
# Handlers & Bot logic
# -------------------------


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = ReplyKeyboardMarkup([["Request Group Ride", "My Rides", "Help"]], resize_keyboard=True)
    await update.message.reply_text("Welcome to TukTuk Group Rides! Use the buttons below to start.", reply_markup=kb)


# ---------- Rider flow (Conversation) ----------
async def request_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = ReplyKeyboardMarkup([[KeyboardButton("Share Location", request_location=True)]], one_time_keyboard=True, resize_keyboard=True)
    await update.message.reply_text("Please share your pickup location (press the button):", reply_markup=kb)
    return PICKUP


async def pickup_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ensure location object
    if not update.message.location:
        await update.message.reply_text("Please use the Share Location button to send pickup coords.")
        return PICKUP
    loc = update.message.location
    context.user_data['pickup_lat'] = loc.latitude
    context.user_data['pickup_lng'] = loc.longitude
    # ask for drop-off (optional)
    kb = ReplyKeyboardMarkup([[KeyboardButton("Share Drop-off Location", request_location=True)], ["Skip"]], one_time_keyboard=True, resize_keyboard=True)
    await update.message.reply_text("Got pickup. Share drop-off location or press Skip.", reply_markup=kb)
    return DROP


async def drop_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # if user shared a geo-location for drop
    if update.message.location:
        loc = update.message.location
        context.user_data['drop_lat'] = loc.latitude
        context.user_data['drop_lng'] = loc.longitude
        context.user_data.pop('drop_text', None)
    else:
        # user typed something (either "Skip" or a text address)
        text = (update.message.text or "").strip()
        if text.lower() == "skip":
            context.user_data['drop_lat'] = None
            context.user_data['drop_lng'] = None
            context.user_data.pop('drop_text', None)
        else:
            # typed dropoff address - we can't geocode here; store text to show to drivers
            context.user_data['drop_lat'] = None
            context.user_data['drop_lng'] = None
            context.user_data['drop_text'] = text

    # ask for group size via inline buttons
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
    data = query.data  # e.g., "group:3"
    _, num = data.split(":")
    context.user_data['group_size'] = int(num)

    # build confirmation summary
    p_lat = context.user_data.get('pickup_lat')
    p_lng = context.user_data.get('pickup_lng')
    d_lat = context.user_data.get('drop_lat')
    d_lng = context.user_data.get('drop_lng')
    d_text = context.user_data.get('drop_text')
    group = context.user_data.get('group_size')

    summary = f"Please confirm your request:\nPickup: ({p_lat:.5f}, {p_lng:.5f})\n"
    if d_lat and d_lng:
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

    # read stored pickup/drop values
    pickup_lat = context.user_data.get('pickup_lat')
    pickup_lng = context.user_data.get('pickup_lng')
    drop_lat = context.user_data.get('drop_lat')
    drop_lng = context.user_data.get('drop_lng')
    drop_text = context.user_data.get('drop_text')
    group_size = context.user_data.get('group_size', 1)

    # create ride in DB
    try:
        ride_id = await db.create_ride(
            rider_tg_id=rider_id,
            pickup_lat=pickup_lat,
            pickup_lng=pickup_lng,
            drop_lat=drop_lat,
            drop_lng=drop_lng,
            drop_text=drop_text,
            group_size=group_size,
        )
    except Exception as e:
        logger.exception("Failed to create ride in DB: %s", e)
        await query.edit_message_text("Failed to create ride. Try again later.")
        return ConversationHandler.END

    await query.edit_message_text("Searching for a driver nearby... ✅")

    # post to dispatch group (if set)
    dispatch_chat = await db.get_setting("dispatch_chat_id")
    if not dispatch_chat:
        await query.message.reply_text(
            "Dispatch group not set. Please ask the admin to run /set_dispatch_group in the driver group."
        )
        return ConversationHandler.END

    # prepare dispatch text
    rider_name = query.from_user.first_name or ""
    dispatch_text = (
        f"🚖 New Ride Request (ID:{ride_id})\n"
        f"Rider: {rider_name} (tg: {rider_id})\n"
        f"Pickup: ({pickup_lat:.5f}, {pickup_lng:.5f})\n"
        f"Group size: {group_size}\nPayment: Cash"
    )
    # if drop text or coords present, include
    if drop_text:
        dispatch_text += f"\nDrop (text): {drop_text}"
    elif drop_lat and drop_lng:
        dispatch_text += f"\nDrop: ({drop_lat:.5f}, {drop_lng:.5f})"

    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Accept", callback_data=f"accept:{ride_id}")]])
    try:
        await context.bot.send_message(chat_id=int(dispatch_chat), text=dispatch_text, reply_markup=kb)
        await context.bot.send_message(chat_id=rider_id, text="Request posted to drivers. We'll notify you when someone accepts.")
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
    data = query.data
    if not data or not data.startswith("accept:"):
        await query.edit_message_text("Invalid accept action.")
        return

    _, ride_id_s = data.split(":")
    try:
        ride_id = int(ride_id_s)
    except ValueError:
        await query.edit_message_text("Invalid ride id.")
        return

    user = query.from_user

    # check that user is a registered driver
    drv = await db.get_driver_by_tg(user.id)
    if not drv:
        await query.edit_message_text("Only registered drivers can accept rides. Please register with /driver_start.")
        return

    driver_db_id = drv["id"]

    # attempt to assign atomically
    assigned = await db.assign_ride_if_unassigned(ride_id, driver_db_id)
    if not assigned:
        await query.edit_message_text("Sorry — this ride was already taken by another driver.")
        return

    # success: inform driver & rider
    ride = await db.get_ride(ride_id)
    rider_tg_id = ride["rider_tg_id"]

    assigned_text = f"✅ Ride {ride_id} assigned to driver {drv.get('name','Unknown')} (tel: {drv.get('phone','N/A')})."
    try:
        await query.edit_message_text(assigned_text)
    except Exception:
        # message might be old or changed — ignore
        pass

    # Notify rider
    try:
        await context.bot.send_message(chat_id=rider_tg_id,
                                       text=f"Driver assigned ✅\nName: {drv.get('name','')}\nPhone: {drv.get('phone','')}\nVehicle: {drv.get('reg_no','')}\nPlease wait for the driver to arrive.")
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
    # this command must be run inside the group that will serve as dispatch group
    uid = update.effective_user.id
    if uid != ADMIN_ID:
        await update.message.reply_text("Only the admin can set the dispatch group.")
        return

    chat_id = update.effective_chat.id
    try:
        await db.set_setting("dispatch_chat_id", str(chat_id))
        await update.message.reply_text(f"Dispatch group saved (chat_id: {chat_id}). Drivers will receive ride requests here.")
    except Exception as e:
        logger.exception("Failed to save dispatch group: %s", e)
        await update.message.reply_text("Failed to save dispatch group.")


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
    # save driver to DB
    try:
        await db.add_or_update_driver(tg_id, name=context.user_data.get('drv_name'), phone=phone, reg_no=context.user_data.get('drv_reg'))
        await update.message.reply_text("Thanks — you're registered. Use /go_online to set yourself online and share location.")
    except Exception as e:
        logger.exception("Failed to register driver: %s", e)
        await update.message.reply_text("Failed to register. Try again later.")
    return ConversationHandler.END


# ---------- Driver actions ----------
async def go_online(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = update.effective_user.id
    drv = await db.get_driver_by_tg(tg_id)
    if not drv:
        await update.message.reply_text("You're not registered. Run /driver_start to register first.")
        return
    try:
        await db.set_driver_status(tg_id, "online")
        kb = ReplyKeyboardMarkup([[KeyboardButton("Share Location", request_location=True)]], one_time_keyboard=True, resize_keyboard=True)
        await update.message.reply_text("You're now ONLINE. Please share your current location so the system can find you for nearby rides.", reply_markup=kb)
    except Exception as e:
        logger.exception("Failed to set driver online: %s", e)
        await update.message.reply_text("Failed to go online. Try again later.")


# capture driver location updates (works for both drivers & riders if they share location)
async def location_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.location:
        await update.message.reply_text("Please use the location sharing button to send coordinates.")
        return

    user = update.effective_user
    loc = update.message.location
    try:
        drv = await db.get_driver_by_tg(user.id)
        if drv:
            await db.update_driver_location(user.id, loc.latitude, loc.longitude)
            await update.message.reply_text("Location updated.")
        else:
            await update.message.reply_text("Location received.")
    except Exception as e:
        logger.exception("Failed to update location: %s", e)
        await update.message.reply_text("Failed to update location.")


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

    try:
        await db.set_ride_status(ride_id, "completed")
        await update.message.reply_text(f"Ride {ride_id} marked as completed. Thanks!")
    except Exception as e:
        logger.exception("Failed to mark ride complete: %s", e)
        await update.message.reply_text("Failed to mark ride complete.")


# ---------- My Rides & Help ----------
async def my_rides(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rider_id = update.effective_user.id
    try:
        rides = await db.get_rides_by_rider(rider_id)
    except Exception as e:
        logger.exception("Failed to fetch rides: %s", e)
        await update.message.reply_text("Failed to fetch rides.")
        return

    if not rides:
        await update.message.reply_text("You have no rides yet.")
        return

    lines = []
    for r in rides:
        ride_id = r.get("id")
        p_lat = r.get("pickup_lat")
        p_lng = r.get("pickup_lng")
        d_lat = r.get("drop_lat")
        d_lng = r.get("drop_lng")
        d_text = r.get("drop_text")
        group_size = r.get("group_size")
        status = r.get("status")
        assigned_driver_id = r.get("assigned_driver_id")
        created_at_ts = r.get("created_at")
        created = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(created_at_ts)) if created_at_ts else ""
        line = f"Ride ID: {ride_id}\nStatus: {status}\nGroup: {group_size}\nPickup: ({p_lat:.5f}, {p_lng:.5f})"
        if d_lat and d_lng:
            line += f"\nDrop: ({d_lat:.5f}, {d_lng:.5f})"
        elif d_text:
            line += f"\nDrop (text): {d_text}"
        if assigned_driver_id:
            drv = await db.get_driver_by_id(assigned_driver_id)
            if drv:
                line += f"\nDriver: {drv.get('name')} | {drv.get('phone')} | {drv.get('reg_no')}"
            else:
                line += f"\nDriver ID: {assigned_driver_id}"
        line += f"\nCreated: {created}"
        lines.append(line)

    # send as one message (could be paginated later)
    msg = "\n\n".join(lines)
    await update.message.reply_text(msg)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (
        "TukTuk Bot Help\n\n"
        "Use the buttons:\n"
        "• Request Group Ride — start a new group ride (share location, choose group size, confirm).\n"
        "• My Rides — see your recent rides and statuses.\n"
        "• Help — show this message.\n\n"
        "Driver commands:\n"
        "/driver_start — register as a driver\n"
        "/go_online — set yourself online (drivers)\n"
        "/complete_ride <ride_id> — mark ride complete\n\n"
        "Admin:\n"
        "/set_dispatch_group — set the group where drivers receive requests\n"
    )
    await update.message.reply_text(txt)


# -------------------------
# Setup application & handlers
# -------------------------
def build_application() -> Application:
    app = Application.builder().token(TOKEN).build()

    # basic handlers
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("set_dispatch_group", set_dispatch_group))

    # rider conversation (supports keyboard entry or command)
    ride_conv = ConversationHandler(
        entry_points=[
            CommandHandler("request", request_start),
            MessageHandler(filters.Regex(r"^Request Group Ride$"), request_start),
        ],
        states={
            PICKUP: [MessageHandler(filters.LOCATION, pickup_received)],
            DROP: [MessageHandler(filters.LOCATION | filters.Regex("^Skip$") | filters.TEXT, drop_received)],
            GROUP: [CallbackQueryHandler(group_callback, pattern="^group:")],
            CONFIRM: [CallbackQueryHandler(confirm_callback, pattern="^confirm:")],
        },
        fallbacks=[CommandHandler("cancel", cancel_conv)],
        per_message=False,
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
        fallbacks=[CommandHandler("cancel", cancel_conv)],
        per_message=False,
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

    return app


# -------------------------
# Main entrypoint (async)
# -------------------------
async def async_main():
    # initialize DB pool and tables
    await db.init()

    # build and run application
    app = build_application()

    logger.info("Starting bot polling...")
    # run_polling is a coroutine in PTB v20; it will run until cancelled
    await app.run_polling()

if __name__ == "__main__":
    try:
        asyncio.run(async_main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down...")
