import logging, time
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler, MessageHandler, filters
from .utils import mk_location_keyboard, group_size_buttons, confirm_buttons, accept_button_for_ride, main_menu_keyboard
logger = logging.getLogger('tuktuk_rides')

PICKUP, DROP, GROUP, CONFIRM = range(4)

async def request_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = mk_location_keyboard()
    await update.message.reply_text('Please share your pickup location (press the button):', reply_markup=kb)
    return PICKUP

async def pickup_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.location:
        await update.message.reply_text('Please use the Share Location button to send pickup coords.')
        return PICKUP
    loc = update.message.location
    context.user_data['pickup_lat'] = loc.latitude
    context.user_data['pickup_lng'] = loc.longitude
    kb = ReplyKeyboardMarkup([[KeyboardButton('Share Drop-off Location', request_location=True)], ['Skip']], one_time_keyboard=True, resize_keyboard=True)
    await update.message.reply_text('Got pickup. Share drop-off location or type address or press Skip.', reply_markup=kb)
    return DROP

async def drop_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.location:
        loc = update.message.location
        context.user_data['drop_lat'] = loc.latitude
        context.user_data['drop_lng'] = loc.longitude
        context.user_data.pop('drop_text', None)
    else:
        text = (update.message.text or '').strip()
        if text.lower() == 'skip':
            context.user_data['drop_lat'] = None
            context.user_data['drop_lng'] = None
            context.user_data.pop('drop_text', None)
        else:
            context.user_data['drop_lat'] = None
            context.user_data['drop_lng'] = None
            context.user_data['drop_text'] = text
    await update.message.reply_text('How many people in your group?', reply_markup=group_size_buttons())
    return GROUP

async def group_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, num = query.data.split(':')
    context.user_data['group_size'] = int(num)
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
        summary += 'Drop: Not provided\n'
    summary += f'Group size: {group}\nPayment: Cash'
    await query.edit_message_text(summary, reply_markup=confirm_buttons())
    return CONFIRM

async def confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == 'confirm:no':
        await query.edit_message_text('Request cancelled.', reply_markup=main_menu_keyboard())
        return ConversationHandler.END
    db = context.bot_data.get('db')
    dispatch_chat = await db.get_setting('dispatch_chat_id')
    if not dispatch_chat:
        await query.edit_message_text('Dispatch group not set. Admin must run /set_dispatch_group in the driver group.', reply_markup=main_menu_keyboard())
        return ConversationHandler.END
    rider_id = query.from_user.id
    pickup_lat = context.user_data.get('pickup_lat')
    pickup_lng = context.user_data.get('pickup_lng')
    drop_lat = context.user_data.get('drop_lat')
    drop_lng = context.user_data.get('drop_lng')
    drop_text = context.user_data.get('drop_text')
    group_size = context.user_data.get('group_size', 1)
    try:
        ride_id = await db.create_ride(rider_tg_id=rider_id, pickup_lat=pickup_lat, pickup_lng=pickup_lng, drop_lat=drop_lat, drop_lng=drop_lng, drop_text=drop_text, group_size=group_size)
    except Exception as e:
        logger.exception('Failed to create ride in DB: %s', e)
        await query.edit_message_text('Failed to create ride. Try again later.', reply_markup=main_menu_keyboard())
        return ConversationHandler.END
    await query.edit_message_text('Searching for a driver nearby... ✅')
    rider_name = query.from_user.first_name or ''
    dispatch_text = f"🚖 New Ride Request (ID:{ride_id})\nRider: {rider_name} (tg: {rider_id})\nPickup: ({pickup_lat:.5f}, {pickup_lng:.5f})\nGroup size: {group_size}\nPayment: Cash"
    if drop_text:
        dispatch_text += f"\nDrop (text): {drop_text}"
    elif drop_lat and drop_lng:
        dispatch_text += f"\nDrop: ({drop_lat:.5f}, {drop_lng:.5f})"
    kb = accept_button_for_ride(ride_id)
    try:
        await context.bot.send_message(chat_id=int(dispatch_chat), text=dispatch_text, reply_markup=kb)
        await context.bot.send_message(chat_id=rider_id, text='✅ Request posted to drivers. We\'ll notify you when someone accepts.', reply_markup=main_menu_keyboard())
    except Exception as e:
        logger.warning('Failed to post to dispatch or notify rider: %s', e)
    return ConversationHandler.END

async def cancel_conv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('Request cancelled.', reply_markup=main_menu_keyboard())
    return ConversationHandler.END

async def accept_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if not data or not data.startswith('accept:'):
        await query.edit_message_text('Invalid action.')
        return
    _, ride_id_s = data.split(':')
    try:
        ride_id = int(ride_id_s)
    except ValueError:
        await query.edit_message_text('Invalid ride id.')
        return
    db = context.bot_data.get('db')
    user = query.from_user
    drv = await db.get_driver_by_tg(user.id)
    if not drv:
        await query.edit_message_text('Only registered drivers can accept rides. Please register with /driver_start.')
        return
    driver_db_id = drv['id']
    assigned = await db.assign_ride_if_unassigned(ride_id, driver_db_id)
    if not assigned:
        await query.edit_message_text('Sorry — this ride was already taken by another driver.')
        return
    ride = await db.get_ride(ride_id)
    rider_tg_id = ride['rider_tg_id']
    assigned_text = f"✅ Ride {ride_id} assigned to driver {drv.get('name','Unknown')} (tel: {drv.get('phone','N/A')})."
    try:
        await query.edit_message_text(assigned_text)
    except Exception:
        pass
    try:
        await context.bot.send_message(chat_id=rider_tg_id, text=f"Driver assigned ✅\nName: {drv.get('name','')}\nPhone: {drv.get('phone','')}\nVehicle: {drv.get('reg_no','')}\nPlease wait for the driver to arrive.", reply_markup=main_menu_keyboard())
    except Exception as e:
        logger.warning('Failed to notify rider: %s', e)
    try:
        await context.bot.send_message(chat_id=user.id, text=f"You accepted ride {ride_id}.\nPickup coords: ({ride['pickup_lat']:.5f}, {ride['pickup_lng']:.5f})\nUse /complete_ride {ride_id} when done.", reply_markup=main_menu_keyboard())
    except Exception as e:
        logger.warning('Failed to DM driver: %s', e)

async def go_online(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = update.effective_user.id
    db = context.bot_data.get('db')
    drv = await db.get_driver_by_tg(tg_id)
    if not drv:
        await update.message.reply_text('You\'re not registered. Run /driver_start to register first.', reply_markup=main_menu_keyboard())
        return
    await db.set_driver_status(tg_id, 'online')
    kb = mk_location_keyboard()
    await update.message.reply_text('You\'re now ONLINE. Please share your current location so the system can find you for nearby rides.', reply_markup=kb)

async def location_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.location:
        await update.message.reply_text('Please use the location sharing button.')
        return
    user = update.effective_user
    loc = update.message.location
    db = context.bot_data.get('db')
    drv = await db.get_driver_by_tg(user.id)
    if drv:
        await db.update_driver_location(user.id, loc.latitude, loc.longitude)
        await update.message.reply_text('Location updated.', reply_markup=main_menu_keyboard())
    else:
        await update.message.reply_text('Location received.', reply_markup=main_menu_keyboard())

async def complete_ride_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text('Usage: /complete_ride <ride_id>')
        return
    try:
        ride_id = int(args[0])
    except ValueError:
        await update.message.reply_text('Invalid ride id.')
        return
    db = context.bot_data.get('db')
    await db.set_ride_status(ride_id, 'completed')
    await update.message.reply_text(f'Ride {ride_id} marked as completed. Thanks!', reply_markup=main_menu_keyboard())
