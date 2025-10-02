#!/usr/bin/env python3
"""
tuktuk_bot.py
Full TukTuk Group Rides bot (Postgres-backed, async).

- Single-file implementation with DB auto-migrations, pagination for histories,
  driver & rider flows, accept/complete buttons, and runtime fixes.
"""

import os
import sys
import logging
import asyncio
import time
from typing import Optional, Dict, Any, List, Tuple

import nest_asyncio
import asyncpg

from telegram import (
    Update,
    KeyboardButton,
    ReplyKeyboardMarkup,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# -------------------------
# Logging
# -------------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger("tuktuk_bot")

# -------------------------
# Conversation states
# -------------------------
PICKUP, DROP, GROUP, CONFIRM = range(4)
DRV_NAME, DRV_REG, DRV_PHONE = range(10, 13)

# -------------------------
# Config from environment
# -------------------------
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ADMIN_ID_ENV = os.environ.get("ADMIN_ID")
DATABASE_URL = os.environ.get("DATABASE_URL")

if not TOKEN:
    logger.error("TELEGRAM_BOT_TOKEN is not set in environment. Exiting.")
    sys.exit(1)
if not ADMIN_ID_ENV:
    logger.error("ADMIN_ID is not set in environment. Exiting.")
    sys.exit(1)
if not DATABASE_URL:
    logger.error("DATABASE_URL is not set in environment. Exiting.")
    sys.exit(1)

try:
    ADMIN_ID = int(ADMIN_ID_ENV)
except Exception as e:
    logger.error("ADMIN_ID must be numeric. Error: %s", e)
    sys.exit(1)

# -------------------------
# DB helper class
# -------------------------
class AsyncDB:
    def __init__(self, dsn: str):
        self.dsn = dsn
        self.pool: Optional[asyncpg.Pool] = None

    async def init(self):
        logger.info("Creating asyncpg pool...")
        self.pool = await asyncpg.create_pool(dsn=self.dsn, min_size=1, max_size=6)
        logger.info("Pool created. Ensuring tables...")
        await self._create_tables()
        logger.info("Database ready.")

    async def close(self):
        if self.pool:
            await self.pool.close()
            self.pool = None

    async def _create_tables(self):
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

    # settings
    async def set_setting(self, k: str, v: str):
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO settings (k,v) VALUES ($1,$2) ON CONFLICT (k) DO UPDATE SET v=EXCLUDED.v;",
                k, v
            )

    async def get_setting(self, k: str) -> Optional[str]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT v FROM settings WHERE k=$1;", k)
            return row['v'] if row else None

    # drivers
    async def add_or_update_driver(self, tg_id: int, name: Optional[str] = None, phone: Optional[str] = None, reg_no: Optional[str] = None):
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO drivers (telegram_id, name, phone, reg_no, status)
                VALUES ($1,$2,$3,$4,'offline')
                ON CONFLICT (telegram_id) DO UPDATE
                SET name = COALESCE(EXCLUDED.name, drivers.name),
                    phone = COALESCE(EXCLUDED.phone, drivers.phone),
                    reg_no = COALESCE(EXCLUDED.reg_no, drivers.reg_no);
                """,
                tg_id, name, phone, reg_no
            )

    async def set_driver_status(self, tg_id: int, status: str):
        async with self.pool.acquire() as conn:
            await conn.execute("UPDATE drivers SET status=$1 WHERE telegram_id=$2;", status, tg_id)

    async def update_driver_location(self, tg_id: int, lat: float, lng: float):
        async with self.pool.acquire() as conn:
            await conn.execute("UPDATE drivers SET lat=$1, lng=$2 WHERE telegram_id=$3;", lat, lng, tg_id)

    async def get_driver_by_tg(self, tg_id: int) -> Optional[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            r = await conn.fetchrow("SELECT * FROM drivers WHERE telegram_id=$1;", tg_id)
            return dict(r) if r else None

    async def get_driver_by_id(self, driver_id: int) -> Optional[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            r = await conn.fetchrow("SELECT * FROM drivers WHERE id=$1;", driver_id)
            return dict(r) if r else None

    async def get_online_drivers(self) -> List[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM drivers WHERE status='online';")
            return [dict(r) for r in rows]

    # rides
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
            return int(row['id'])

    async def get_ride(self, ride_id: int) -> Optional[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            r = await conn.fetchrow("SELECT * FROM rides WHERE id=$1;", ride_id)
            return dict(r) if r else None

    async def get_rides_by_rider(self, rider_tg_id: int, offset: int = 0, limit: int = 5) -> Tuple[List[Dict[str, Any]], int]:
        """
        returns (rows, total_count)
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, pickup_lat, pickup_lng, drop_lat, drop_lng, drop_text, group_size, status, assigned_driver_id, created_at
                FROM rides WHERE rider_tg_id=$1 ORDER BY created_at DESC OFFSET $2 LIMIT $3;
                """, rider_tg_id, offset, limit
            )
            total = await conn.fetchval("SELECT COUNT(1) FROM rides WHERE rider_tg_id=$1;", rider_tg_id)
            return [dict(r) for r in rows], int(total or 0)

    async def get_rides_by_driver(self, driver_id: int, offset: int = 0, limit: int = 5) -> Tuple[List[Dict[str, Any]], int]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, rider_tg_id, pickup_lat, pickup_lng, drop_lat, drop_lng, drop_text, group_size, status, created_at
                FROM rides WHERE assigned_driver_id=$1 ORDER BY created_at DESC OFFSET $2 LIMIT $3;
                """, driver_id, offset, limit
            )
            total = await conn.fetchval("SELECT COUNT(1) FROM rides WHERE assigned_driver_id=$1;", driver_id)
            return [dict(r) for r in rows], int(total or 0)

    async def assign_ride_if_unassigned(self, ride_id: int, driver_id: int) -> bool:
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE rides SET assigned_driver_id=$1, status='assigned'
                WHERE id=$2 AND assigned_driver_id IS NULL;
                """, driver_id, ride_id
            )
            # returns like 'UPDATE <n>'
            try:
                return int(result.split()[-1]) > 0
            except Exception:
                return False

    async def set_ride_status(self, ride_id: int, status: str):
        async with self.pool.acquire() as conn:
            await conn.execute("UPDATE rides SET status=$1 WHERE id=$2;", status, ride_id)

    async def get_recent_searching_rides(self, limit: int = 10) -> List[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM rides WHERE status='searching' ORDER BY created_at ASC LIMIT $1;", limit)
            return [dict(r) for r in rows]

# global db
db = AsyncDB(DATABASE_URL)

# -------------------------
# Utility helpers
# -------------------------
def google_maps_link(lat: float, lng: float) -> str:
    return f"https://www.google.com/maps/search/?api=1&query={lat:.6f},{lng:.6f}"

def format_ride_summary(r: Dict[str, Any]) -> str:
    """
    Build a short ride summary string for messages.
    """
    rid = r.get('id')
    status = r.get('status') or ''
    group = r.get('group_size') or ''
    created = r.get('created_at')
    created_ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(created)) if created else ""
    pickup_lat = r.get('pickup_lat')
    pickup_lng = r.get('pickup_lng')
    drop_lat = r.get('drop_lat')
    drop_lng = r.get('drop_lng')
    drop_text = r.get('drop_text')
    s = f"Ride ID: {rid}\nStatus: {status}\nGroup: {group}\nCreated: {created_ts}\n"
    if pickup_lat and pickup_lng:
        s += f"Pickup: ({pickup_lat:.5f},{pickup_lng:.5f})\nMaps: {google_maps_link(pickup_lat, pickup_lng)}\n"
    if drop_lat and drop_lng:
        s += f"Drop: ({drop_lat:.5f},{drop_lng:.5f})\nMaps: {google_maps_link(drop_lat, drop_lng)}\n"
    elif drop_text:
        s += f"Drop (text): {drop_text}\n"
    return s

# -------------------------
# Handlers
# -------------------------
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = ReplyKeyboardMarkup([["Request Group Ride", "My Rides", "Help"]], resize_keyboard=True)
    await update.message.reply_text("Welcome to TukTuk Group Rides! Use the buttons below to start.", reply_markup=kb)

# Rider conversation
async def request_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = ReplyKeyboardMarkup([[KeyboardButton("Share Location", request_location=True)]], one_time_keyboard=True, resize_keyboard=True)
    await update.message.reply_text("Please share your pickup location (press the button):", reply_markup=kb)
    return PICKUP

async def pickup_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.location:
        await update.message.reply_text("Please share your pickup location using the button.")
        return PICKUP
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
        context.user_data.pop('drop_text', None)
    else:
        text = (update.message.text or "").strip()
        if text.lower() == "skip":
            context.user_data['drop_lat'] = None
            context.user_data['drop_lng'] = None
            context.user_data.pop('drop_text', None)
        else:
            context.user_data['drop_lat'] = None
            context.user_data['drop_lng'] = None
            context.user_data['drop_text'] = text

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
    group = context.user_data.get('group_size', 1)

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
    pickup_lat = context.user_data.get('pickup_lat')
    pickup_lng = context.user_data.get('pickup_lng')
    drop_lat = context.user_data.get('drop_lat')
    drop_lng = context.user_data.get('drop_lng')
    drop_text = context.user_data.get('drop_text')
    group_size = context.user_data.get('group_size', 1)

    try:
        ride_id = await db.create_ride(
            rider_tg_id=rider_id,
            pickup_lat=pickup_lat,
            pickup_lng=pickup_lng,
            drop_lat=drop_lat,
            drop_lng=drop_lng,
            drop_text=drop_text,
            group_size=group_size
        )
    except Exception as e:
        logger.exception("create_ride failed: %s", e)
        await query.edit_message_text("Failed to create ride. Try again later.")
        return ConversationHandler.END

    await query.edit_message_text("Searching for a driver nearby... ✅")

    dispatch_chat = await db.get_setting("dispatch_chat_id")
    if not dispatch_chat:
        await query.message.reply_text("Dispatch group not set. Ask admin to run /set_dispatch_group in driver group.")
        return ConversationHandler.END

    dispatch_text = (
        f"🚖 New Ride Request (ID:{ride_id})\n"
        f"Rider: {query.from_user.first_name} (tg: {rider_id})\n"
        f"Pickup: ({pickup_lat:.5f}, {pickup_lng:.5f})\n"
        f"Group size: {group_size}\nPayment: Cash"
    )
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

# Accept callback (drivers)
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
    drv = await db.get_driver_by_tg(user.id)
    if not drv:
        await query.edit_message_text("Only registered drivers can accept rides. Register with /driver_start.")
        return

    driver_db_id = drv['id']
    assigned = await db.assign_ride_if_unassigned(ride_id, driver_db_id)
    if not assigned:
        await query.edit_message_text("Sorry — this ride was already taken by another driver.")
        return

    ride = await db.get_ride(ride_id)
    rider_tg_id = ride['rider_tg_id']

    assigned_text = f"✅ Ride {ride_id} assigned to driver {drv.get('name','Unknown')} (tel: {drv.get('phone','N/A')})."
    try:
        await query.edit_message_text(assigned_text)
    except Exception:
        pass

    # notify rider
    try:
        await context.bot.send_message(chat_id=rider_tg_id,
            text=f"Driver assigned ✅\nName: {drv.get('name','')}\nPhone: {drv.get('phone','')}\nVehicle: {drv.get('reg_no','')}\nPlease wait for the driver to arrive.")
    except Exception as e:
        logger.warning("Failed to notify rider: %s", e)

    # message driver with details + Complete button
    pickup_lat = ride.get('pickup_lat')
    pickup_lng = ride.get('pickup_lng')
    drop_lat = ride.get('drop_lat')
    drop_lng = ride.get('drop_lng')
    summary = format_ride_summary(ride)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Complete Ride", callback_data=f"complete:{ride_id}")],
    ])
    try:
        await context.bot.send_message(chat_id=user.id, text=f"You accepted ride {ride_id}.\n\n{summary}", reply_markup=kb)
    except Exception as e:
        logger.warning("Failed to DM driver: %s", e)

# Complete via button
async def complete_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if not data or not data.startswith("complete:"):
        await query.edit_message_text("Invalid action.")
        return
    _, ride_id_s = data.split(":")
    try:
        ride_id = int(ride_id_s)
    except Exception:
        await query.edit_message_text("Invalid ride id.")
        return

    # mark completed
    try:
        await db.set_ride_status(ride_id, "completed")
        await query.edit_message_text(f"Ride {ride_id} marked as completed. Thanks!")
    except Exception as e:
        logger.exception("Failed to set ride completed: %s", e)
        await query.edit_message_text("Failed to mark as completed. Try /complete_ride <id>.")

# Admin set dispatch group
async def set_dispatch_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Only the admin can set the dispatch group.")
        return
    chat_id = update.effective_chat.id
    try:
        await db.set_setting("dispatch_chat_id", str(chat_id))
        await update.message.reply_text(f"Dispatch group saved (chat_id: {chat_id}). Drivers will receive ride requests here.")
    except Exception as e:
        logger.exception("Failed to save dispatch group: %s", e)
        await update.message.reply_text("Failed to save dispatch group.")

# Driver registration flow
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
    try:
        await db.add_or_update_driver(tg_id, name=context.user_data.get('drv_name'), phone=phone, reg_no=context.user_data.get('drv_reg'))
        await update.message.reply_text("Thanks — you're registered. Use /go_online to set yourself online and share location.")
    except Exception as e:
        logger.exception("Failed to register driver: %s", e)
        await update.message.reply_text("Failed to register. Try again later.")
    return ConversationHandler.END

# go online
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

# location updates
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

# legacy /complete_ride command (still supported)
async def complete_ride_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

# My rides (with pagination)
PAGE_SIZE = 5

def make_rides_callback_data(role: str, user_id: int, page: int) -> str:
    # role: 'rider' or 'driver'
    return f"rides:{role}:{user_id}:{page}"

async def my_rides_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await send_rides_page(role='rider', requester_id=user.id, page=0, context=context)

async def driver_rides_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    drv = await db.get_driver_by_tg(user.id)
    if not drv:
        await update.message.reply_text("You're not a registered driver. Use /driver_start to register.")
        return
    driver_db_id = drv['id']
    await send_rides_page(role='driver', requester_id=driver_db_id, page=0, context=context)

async def send_rides_page(role: str, requester_id: int, page: int, context: ContextTypes.DEFAULT_TYPE, query: Optional[Any] = None):
    offset = page * PAGE_SIZE
    if role == 'rider':
        rows, total = await db.get_rides_by_rider(requester_id, offset=offset, limit=PAGE_SIZE)
    else:  # driver
        rows, total = await db.get_rides_by_driver(requester_id, offset=offset, limit=PAGE_SIZE)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    # build message
    if not rows:
        text = "No rides found."
    else:
        parts = [f"Page {page+1} / {total_pages}\n"]
        for r in rows:
            parts.append(format_ride_summary(r))
            # for rider include driver info if assigned
            if role == 'rider' and r.get('assigned_driver_id'):
                drv = await db.get_driver_by_id(r.get('assigned_driver_id'))
                if drv:
                    parts.append(f"Driver: {drv.get('name')} | {drv.get('phone')} | {drv.get('reg_no')}")
            parts.append("-" * 28)
        text = "\n".join(parts)

    # build inline buttons
    buttons = []
    if page > 0:
        buttons.append(InlineKeyboardButton("⏮ Prev", callback_data=make_rides_callback_data(role, requester_id, page-1)))
    if (page + 1) < total_pages:
        buttons.append(InlineKeyboardButton("Next ⏭", callback_data=make_rides_callback_data(role, requester_id, page+1)))
    buttons.append(InlineKeyboardButton("❌ Close", callback_data=f"rides_close:{role}:{requester_id}"))
    kb = InlineKeyboardMarkup([buttons])

    if query and hasattr(query, "message") and query.message:
        try:
            await query.edit_message_text(text, reply_markup=kb)
            return
        except Exception as e:
            # fallback to sending new message
            logger.debug("Editing message failed: %s", e)

    await context.bot.send_message(chat_id=(query.from_user.id if query else requester_id if role == 'driver' else requester_id), text=text, reply_markup=kb)

async def rides_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    # formats:
    # rides:role:user:page
    # rides_close:role:user
    if data.startswith("rides_close:"):
        # just remove keyboard / message
        try:
            await query.edit_message_text("Closed.")
        except Exception:
            pass
        return

    if not data.startswith("rides:"):
        await query.edit_message_text("Unknown action.")
        return
    _, role, uid_s, page_s = data.split(":")
    try:
        uid = int(uid_s)
        page = int(page_s)
    except Exception:
        await query.edit_message_text("Invalid pagination data.")
        return
    # For riders, requester_id is telegram id; for driver, uid is driver_db_id
    await send_rides_page(role=role, requester_id=uid, page=page, context=context, query=query)

# Help handler
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (
        "TukTuk Bot Help\n\n"
        "Buttons:\n"
        "• Request Group Ride — start a group ride (share location, choose group size, confirm).\n"
        "• My Rides — view your ride history (paginated).\n\n"
        "Driver commands:\n"
        "/driver_start — register as a driver\n"
        "/go_online — set yourself online and share location\n"
        "/driver_rides — view rides assigned to you (paginated)\n\n"
        "Admin:\n"
        "/set_dispatch_group — set the group where drivers receive requests\n"
    )
    await update.message.reply_text(txt)

# -------------------------
# Build application & handlers
# -------------------------
def build_application() -> Application:
    app = Application.builder().token(TOKEN).build()

    # basic
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("set_dispatch_group", set_dispatch_group))

    # rider conversation
    ride_conv = ConversationHandler(
        entry_points=[CommandHandler("request", request_start), MessageHandler(filters.Regex(r"^Request Group Ride$"), request_start)],
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

    # keyboard handlers
    app.add_handler(MessageHandler(filters.Regex(r"^My Rides$"), my_rides_cmd))
    app.add_handler(MessageHandler(filters.Regex(r"^Help$"), help_cmd))
    app.add_handler(MessageHandler(filters.Regex(r"^Request Group Ride$"), request_start))

    # driver commands
    app.add_handler(CommandHandler("go_online", go_online))
    app.add_handler(MessageHandler(filters.LOCATION, location_handler))  # general location handler
    app.add_handler(CommandHandler("complete_ride", complete_ride_cmd))
    app.add_handler(CommandHandler("my_rides", my_rides_cmd))
    app.add_handler(CommandHandler("driver_rides", driver_rides_cmd))

    # callbacks
    app.add_handler(CallbackQueryHandler(accept_callback, pattern="^accept:"))
    app.add_handler(CallbackQueryHandler(complete_button_callback, pattern="^complete:"))
    app.add_handler(CallbackQueryHandler(rides_callback_handler, pattern="^rides:|^rides_close:"))

    return app

# -------------------------
# Main entrypoint
# -------------------------
async def async_main():
    # init db
    try:
        await db.init()
    except Exception as e:
        logger.exception("DB initialization failed: %s", e)
        raise

    app = build_application()
    logger.info("Starting bot (polling)...")
    # run polling until stopped
    await app.run_polling()

if __name__ == "__main__":
    # Use nest_asyncio to avoid "event loop already running" errors on hosts that manage their own loop.
    nest_asyncio.apply()

    try:
        asyncio.run(async_main())
    except RuntimeError as e:
        # fallback if loop is already running
        logger.warning("asyncio.run failed (loop running): %s — falling back to get_event_loop()", e)
        loop = asyncio.get_event_loop()
        loop.run_until_complete(async_main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down...")
    except Exception as exc:
        logger.exception("Fatal error in main: %s", exc)
