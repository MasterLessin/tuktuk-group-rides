# handlers.py
import time
import logging
from typing import Dict, Any, Optional

from telegram import (
    Update,
    KeyboardButton,
    ReplyKeyboardMarkup,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
    ContextTypes,
)

logger = logging.getLogger("tuktuk_handlers")

# conversation states should match main module usage
PICKUP, DROP, GROUP, CONFIRM = range(4)
DRV_NAME, DRV_REG, DRV_PHONE = range(10, 13)

PAGE_SIZE = 5


def google_maps_link(lat: float, lng: float) -> str:
    return f"https://www.google.com/maps/search/?api=1&query={lat:.6f},{lng:.6f}"


def format_ride_summary(r: Dict[str, Any]) -> str:
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


def make_rides_callback_data(role: str, user_id: int, page: int) -> str:
    return f"rides:{role}:{user_id}:{page}"


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = ReplyKeyboardMarkup([["Request Group Ride", "My Rides", "Help"]], resize_keyboard=True)
    await update.message.reply_text("Welcome to TukTuk Group Rides! Use the buttons below to start.", reply_markup=kb)


# Rider conversation handlers
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
        ride_id = await context.bot_data['db'].create_ride(
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

    dispatch_chat = await context.bot_data['db'].get_setting("dispatch_chat_id")
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


# Accept and complete callbacks
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
    db = context.bot_data['db']
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

    try:
        await context.bot.send_message(chat_id=rider_tg_id,
            text=f"Driver assigned ✅\nName: {drv.get('name','')}\nPhone: {drv.get('phone','')}\nVehicle: {drv.get('reg_no','')}\nPlease wait for the driver to arrive.")
    except Exception:
        logger.warning("Failed to notify rider.")

    # DM driver with details + complete button
    summary = format_ride_summary(ride)
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Complete Ride", callback_data=f"complete:{ride_id}")]])
    try:
        await context.bot.send_message(chat_id=user.id, text=f"You accepted ride {ride_id}.\n\n{summary}", reply_markup=kb)
    except Exception:
        logger.warning("Failed to DM driver.")

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

    try:
        await context.bot_data['db'].set_ride_status(ride_id, "completed")
        await query.edit_message_text(f"Ride {ride_id} marked as completed. Thanks!")
    except Exception as e:
        logger.exception("Failed to set ride completed: %s", e)
        await query.edit_message_text("Failed to mark as completed. Try /complete_ride <id>.")


# admin: set dispatch group
async def set_dispatch_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != context.bot_data['ADMIN_ID']:
        await update.message.reply_text("Only the admin can set the dispatch group.")
        return
    chat_id = update.effective_chat.id
    try:
        await context.bot_data['db'].set_setting("dispatch_chat_id", str(chat_id))
        await update.message.reply_text(f"Dispatch group saved (chat_id: {chat_id}). Drivers will receive ride requests here.")
    except Exception as e:
        logger.exception("Failed to save dispatch group: %s", e)
        await update.message.reply_text("Failed to save dispatch group.")


# driver registration conversation
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
        await context.bot_data['db'].add_or_update_driver(tg_id, name=context.user_data.get('drv_name'), phone=phone, reg_no=context.user_data.get('drv_reg'))
        await update.message.reply_text("Thanks — you're registered. Use /go_online to set yourself online and share location.")
    except Exception as e:
        logger.exception("Failed to register driver: %s", e)
        await update.message.reply_text("Failed to register. Try again later.")
    return ConversationHandler.END

# go_online and location handlers
async def go_online(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = update.effective_user.id
    db = context.bot_data['db']
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


async def location_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.location:
        await update.message.reply_text("Please use the location sharing button to send coordinates.")
        return
    tg_id = update.effective_user.id
    loc = update.message.location
    db = context.bot_data['db']
    try:
        drv = await db.get_driver_by_tg(tg_id)
        if drv:
            await db.update_driver_location(tg_id, loc.latitude, loc.longitude)
            await update.message.reply_text("Location updated.")
        else:
            await update.message.reply_text("Location received.")
    except Exception as e:
        logger.exception("Failed to update location: %s", e)
        await update.message.reply_text("Failed to update location.")


# /complete_ride command (fallback)
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
        await context.bot_data['db'].set_ride_status(ride_id, "completed")
        await update.message.reply_text(f"Ride {ride_id} marked as completed. Thanks!")
    except Exception as e:
        logger.exception("Failed to mark ride complete: %s", e)
        await update.message.reply_text("Failed to mark ride complete.")


# ride history (rider & driver) with pagination
async def my_rides_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await send_rides_page(role='rider', requester_id=user.id, page=0, context=context)


async def driver_rides_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db = context.bot_data['db']
    drv = await db.get_driver_by_tg(user.id)
    if not drv:
        await update.message.reply_text("You're not a registered driver. Use /driver_start to register.")
        return
    await send_rides_page(role='driver', requester_id=drv['id'], page=0, context=context)


async def send_rides_page(role: str, requester_id: int, page: int, context: ContextTypes.DEFAULT_TYPE, query=None):
    offset = page * PAGE_SIZE
    db = context.bot_data['db']
    if role == 'rider':
        rows, total = await db.get_rides_by_rider(requester_id, offset=offset, limit=PAGE_SIZE)
    else:
        rows, total = await db.get_rides_by_driver(requester_id, offset=offset, limit=PAGE_SIZE)

    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    if not rows:
        text = "No rides found."
    else:
        parts = [f"Page {page+1} / {total_pages}\n"]
        for r in rows:
            parts.append(format_ride_summary(r))
            if role == 'rider' and r.get('assigned_driver_id'):
                drv = await db.get_driver_by_id(r.get('assigned_driver_id'))
                if drv:
                    parts.append(f"Driver: {drv.get('name')} | {drv.get('phone')} | {drv.get('reg_no')}")
            parts.append("-" * 28)
        text = "\n".join(parts)

    buttons = []
    if page > 0:
        buttons.append(InlineKeyboardButton("⏮ Prev", callback_data=make_rides_callback_data(role, requester_id, page-1)))
    if (page + 1) < total_pages:
        buttons.append(InlineKeyboardButton("Next ⏭", callback_data=make_rides_callback_data(role, requester_id, page+1)))
    buttons.append(InlineKeyboardButton("❌ Close", callback_data=f"rides_close:{role}:{requester_id}"))
    kb = InlineKeyboardMarkup([buttons])

    if query:
        try:
            await query.edit_message_text(text, reply_markup=kb)
            return
        except Exception:
            logger.debug("Failed to edit message for pagination, will send new message.")

    await context.bot.send_message(chat_id=query.from_user.id if query else (requester_id if role == 'driver' else requester_id), text=text, reply_markup=kb)


async def rides_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data.startswith("rides_close:"):
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
        uid = int(uid_s); page = int(page_s)
    except Exception:
        await query.edit_message_text("Invalid pagination data.")
        return
    await send_rides_page(role=role, requester_id=uid, page=page, context=context, query=query)


# Register handlers into an Application
def register_handlers(app, db_obj, ADMIN_ID: int):
    """
    Registers all handlers on `app` and stores DB + ADMIN into app.bot_data
    """
    # store db + admin in bot_data for handlers to use
    app.bot_data['db'] = db_obj
    app.bot_data['ADMIN_ID'] = ADMIN_ID

    # simple commands
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("help", lambda u, c: c.bot.send_message(chat_id=u.effective_chat.id, text=(
        "Help:\n"
        "/request - request a ride\n"
        "/my_rides - your rides\n"
        "/driver_start - register as driver\n"
        "/driver_rides - your assigned rides (drivers)\n"
        "/set_dispatch_group - admin only"
    ))))

    app.add_handler(CommandHandler("set_dispatch_group", set_dispatch_group))

    # rider conv
    ride_conv = ConversationHandler(
        entry_points=[CommandHandler("request", request_start), MessageHandler(filters.Regex(r"^Request Group Ride$"), request_start)],
        states={
            PICKUP: [MessageHandler(filters.LOCATION, pickup_received)],
            DROP: [MessageHandler(filters.LOCATION | filters.TEXT & ~filters.COMMAND, drop_received)],
            GROUP: [CallbackQueryHandler(group_callback, pattern="^group:")],
            CONFIRM: [CallbackQueryHandler(confirm_callback, pattern="^confirm:")],
        },
        fallbacks=[CommandHandler("cancel", cancel_conv)],
        per_message=False,
    )
    app.add_handler(ride_conv)

    # driver conv
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

    # location, accept, complete, history
    app.add_handler(CommandHandler("go_online", go_online))
    app.add_handler(MessageHandler(filters.LOCATION, location_handler))  # generic location handler
    app.add_handler(CallbackQueryHandler(accept_callback, pattern="^accept:"))
    app.add_handler(CallbackQueryHandler(complete_button_callback, pattern="^complete:"))
    app.add_handler(CallbackQueryHandler(rides_callback_handler, pattern="^rides:|^rides_close:"))

    app.add_handler(CommandHandler("complete_ride", complete_ride_cmd))
    app.add_handler(CommandHandler("my_rides", my_rides_cmd))
    app.add_handler(CommandHandler("driver_rides", driver_rides_cmd))

    # keyboard buttons mapping
    app.add_handler(MessageHandler(filters.Regex(r"^My Rides$"), my_rides_cmd))
    app.add_handler(MessageHandler(filters.Regex(r"^Help$"), lambda u, c: c.bot.send_message(chat_id=u.effective_chat.id, text="Send /help for available commands.")))
