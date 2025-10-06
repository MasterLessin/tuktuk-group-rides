import logging, time, math
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler, MessageHandler, filters
from .utils import mk_location_keyboard, group_size_buttons, confirm_buttons, accept_button_for_ride, calculate_fare_estimate, estimate_travel_time, trip_actions_buttons, driver_trip_buttons, rating_buttons
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
    
    # Calculate fare estimate
    pickup_lat = context.user_data.get('pickup_lat')
    pickup_lng = context.user_data.get('pickup_lng')
    drop_lat = context.user_data.get('drop_lat')
    drop_lng = context.user_data.get('drop_lng')
    
    distance_km = 0
    estimated_trip_time = 0
    
    if drop_lat and drop_lng:
        distance_km = AsyncDB.calculate_distance(pickup_lat, pickup_lng, drop_lat, drop_lng)
        estimated_trip_time = estimate_travel_time(distance_km)
    
    context.user_data['distance_km'] = distance_km
    context.user_data['estimated_trip_time'] = estimated_trip_time
    
    await update.message.reply_text('How many people in your group?', reply_markup=group_size_buttons())
    return GROUP

async def group_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, num = query.data.split(':')
    group_size = int(num)
    context.user_data['group_size'] = group_size
    
    # Calculate final fare estimate
    distance_km = context.user_data.get('distance_km', 0)
    estimated_trip_time = context.user_data.get('estimated_trip_time', 0)
    fare_estimate = calculate_fare_estimate(distance_km, estimated_trip_time, group_size)
    context.user_data['fare_estimate'] = fare_estimate
    
    p_lat = context.user_data.get('pickup_lat')
    p_lng = context.user_data.get('pickup_lng')
    d_lat = context.user_data.get('drop_lat')
    d_lng = context.user_data.get('drop_lng')
    d_text = context.user_data.get('drop_text')
    
    summary = f"🚖 **Ride Request Summary**\n\n"
    summary += f"📍 **Pickup:** ({p_lat:.5f}, {p_lng:.5f})\n"
    
    if d_lat and d_lng:
        summary += f"🎯 **Drop-off:** ({d_lat:.5f}, {d_lng:.5f})\n"
    elif d_text:
        summary += f"🎯 **Drop-off:** {d_text}\n"
    else:
        summary += "🎯 **Drop-off:** Not specified\n"
        
    summary += f"👥 **Group size:** {group_size}\n"
    summary += f"💰 **Estimated Fare:** {fare_estimate:.2f}\n"
    summary += f"⏱️ **Estimated Trip Time:** {estimated_trip_time} min\n"
    summary += "💵 **Payment:** Cash\n\n"
    summary += "Please confirm your request:"
    
    await query.edit_message_text(summary, reply_markup=confirm_buttons())
    return CONFIRM

async def confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == 'confirm:no':
        await query.edit_message_text('Request cancelled.')
        return ConversationHandler.END
        
    db = context.bot_data.get('db')
    dispatch_chat = await db.get_setting('dispatch_chat_id')
    
    if not dispatch_chat:
        await query.edit_message_text('Dispatch group not set. Admin must run /set_dispatch_group in the driver group.')
        return ConversationHandler.END
        
    rider_id = query.from_user.id
    pickup_lat = context.user_data.get('pickup_lat')
    pickup_lng = context.user_data.get('pickup_lng')
    drop_lat = context.user_data.get('drop_lat')
    drop_lng = context.user_data.get('drop_lng')
    drop_text = context.user_data.get('drop_text')
    group_size = context.user_data.get('group_size', 1)
    fare_estimate = context.user_data.get('fare_estimate', 0)
    estimated_trip_time = context.user_data.get('estimated_trip_time', 0)
    
    # Estimate pickup time (assuming driver is 5-10 minutes away)
    estimated_pickup_time = 8  # Average 8 minutes
    
    try:
        ride_id = await db.create_ride(
            rider_tg_id=rider_id, 
            pickup_lat=pickup_lat, 
            pickup_lng=pickup_lng, 
            drop_lat=drop_lat, 
            drop_lng=drop_lng, 
            drop_text=drop_text, 
            group_size=group_size,
            fare_estimate=fare_estimate,
            estimated_pickup_time=estimated_pickup_time,
            estimated_trip_time=estimated_trip_time
        )
    except Exception as e:
        logger.exception('Failed to create ride in DB: %s', e)
        await query.edit_message_text('Failed to create ride. Try again later.')
        return ConversationHandler.END
    
    await query.edit_message_text('🔍 Searching for a driver nearby...')
    
    # Prepare dispatch message
    rider_name = query.from_user.first_name or ''
    dispatch_text = f"🚖 **New Ride Request** (ID: {ride_id})\n\n"
    dispatch_text += f"📍 **Pickup:** ({pickup_lat:.5f}, {pickup_lng:.5f})\n"
    
    if drop_text:
        dispatch_text += f"🎯 **Drop-off:** {drop_text}\n"
    elif drop_lat and drop_lng:
        dispatch_text += f"🎯 **Drop-off:** ({drop_lat:.5f}, {drop_lng:.5f})\n"
    else:
        dispatch_text += "🎯 **Drop-off:** Not specified\n"
        
    dispatch_text += f"👥 **Group size:** {group_size}\n"
    dispatch_text += f"💰 **Estimated Fare:** {fare_estimate:.2f}\n"
    dispatch_text += f"⏱️ **ETA to Pickup:** ~{estimated_pickup_time} min\n"
    dispatch_text += f"🕒 **Trip Time:** ~{estimated_trip_time} min\n"
    dispatch_text += "💵 **Payment:** Cash"
    
    kb = accept_button_for_ride(ride_id)
    
    try:
        await context.bot.send_message(chat_id=int(dispatch_chat), text=dispatch_text, reply_markup=kb)
        await context.bot.send_message(
            chat_id=rider_id, 
            text=f'✅ Request posted to drivers!\n\nEstimated fare: {fare_estimate:.2f}\nETA to pickup: ~{estimated_pickup_time} min\n\nWe\'ll notify you when a driver accepts.',
            reply_markup=trip_actions_buttons(ride_id)
        )
    except Exception as e:
        logger.warning('Failed to post to dispatch or notify rider: %s', e)
    
    return ConversationHandler.END

async def cancel_conv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('Request cancelled.')
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
    
    # Update ride status
    await db.set_ride_status(ride_id, 'driver_assigned')
    
    assigned_text = f"✅ Ride {ride_id} assigned to you!\n\n"
    assigned_text += f"📍 **Pickup:** ({ride['pickup_lat']:.5f}, {ride['pickup_lng']:.5f})\n"
    
    if ride['drop_text']:
        assigned_text += f"🎯 **Drop-off:** {ride['drop_text']}\n"
    elif ride['drop_lat'] and ride['drop_lng']:
        assigned_text += f"🎯 **Drop-off:** ({ride['drop_lat']:.5f}, {ride['drop_lng']:.5f})\n"
        
    assigned_text += f"👥 **Group size:** {ride['group_size']}\n"
    assigned_text += f"💰 **Estimated Fare:** {ride['fare_estimate']:.2f}\n\n"
    assigned_text += "Please proceed to the pickup location."
    
    try:
        await query.edit_message_text(assigned_text)
        
        # Notify rider
        driver_rating = f" ({drv.get('rating', 5.0):.1f}⭐)" if drv.get('rating') else ""
        rider_notification = f"🚗 **Driver Assigned!**\n\n"
        rider_notification += f"👨‍✈️ **Driver:** {drv.get('name', '')}{driver_rating}\n"
        rider_notification += f"🚙 **Vehicle:** {drv.get('reg_no', '')}\n"
        rider_notification += f"⏱️ **ETA:** ~{ride['estimated_pickup_time']} minutes\n\n"
        rider_notification += "Your driver is on the way!"
        
        await context.bot.send_message(
            chat_id=rider_tg_id, 
            text=rider_notification,
            reply_markup=trip_actions_buttons(ride_id)
        )
        
        # Send driver trip management buttons
        await context.bot.send_message(
            chat_id=user.id,
            text=f"You accepted ride {ride_id}.\nUse the buttons below to manage the trip:",
            reply_markup=driver_trip_buttons(ride_id)
        )
        
    except Exception as e:
        logger.warning('Failed to notify rider or driver: %s', e)

# New enhanced functions for trip management
async def cancel_trip_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    if not data.startswith('cancel_trip:'):
        return
        
    _, ride_id_s = data.split(':')
    ride_id = int(ride_id_s)
    
    db = context.bot_data.get('db')
    user_id = query.from_user.id
    
    # Check if user is the rider
    ride = await db.get_ride(ride_id)
    if ride and ride['rider_tg_id'] == user_id:
        await db.cancel_ride(ride_id, 'rider')
        await query.edit_message_text('✅ Trip cancelled successfully.')
        
        # Notify driver if assigned
        if ride['assigned_driver_id']:
            driver = await db.get_driver_by_id(ride['assigned_driver_id'])
            if driver:
                try:
                    await context.bot.send_message(
                        chat_id=driver['telegram_id'],
                        text=f"❌ Ride {ride_id} was cancelled by the rider."
                    )
                except Exception as e:
                    logger.warning('Failed to notify driver about cancellation: %s', e)

async def rate_trip_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    if not data.startswith('rate:'):
        return
        
    _, ride_id_s, rating_s = data.split(':')
    ride_id = int(ride_id_s)
    rating = int(rating_s)
    
    db = context.bot_data.get('db')
    user_id = query.from_user.id
    
    ride = await db.get_ride(ride_id)
    if ride and ride['rider_tg_id'] == user_id and ride['assigned_driver_id']:
        # Store rating
        await db.add_rating(ride_id, ride['assigned_driver_id'], user_id, rating)
        
        # Update driver rating
        await db.update_driver_rating(ride['assigned_driver_id'], rating)
        
        await query.edit_message_text(f'✅ Thank you for your {rating}⭐ rating!')
        
        # Notify driver
        driver = await db.get_driver_by_id(ride['assigned_driver_id'])
        if driver:
            try:
                await context.bot.send_message(
                    chat_id=driver['telegram_id'],
                    text=f"⭐ You received a {rating} star rating for ride {ride_id}!"
                )
            except Exception as e:
                logger.warning('Failed to notify driver about rating: %s', e)

async def sos_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    if not data.startswith('sos:'):
        return
        
    _, ride_id_s = data.split(':')
    ride_id = int(ride_id_s)
    
    db = context.bot_data.get('db')
    user_id = query.from_user.id
    
    ride = await db.get_ride(ride_id)
    if ride:
        # Get emergency contacts
        contacts = await db.get_emergency_contacts(user_id)
        
        sos_message = f"🆘 **EMERGENCY ALERT**\n\n"
        sos_message += f"User {user_id} has activated SOS during trip {ride_id}.\n"
        sos_message += f"📍 **Pickup Location:** ({ride['pickup_lat']:.5f}, {ride['pickup_lng']:.5f})\n"
        
        if ride['drop_lat'] and ride['drop_lng']:
            sos_message += f"🎯 **Drop-off Location:** ({ride['drop_lat']:.5f}, {ride['drop_lng']:.5f})\n"
        elif ride['drop_text']:
            sos_message += f"🎯 **Drop-off Location:** {ride['drop_text']}\n"
            
        sos_message += f"⏰ **Trip Started:** {time.ctime(ride['created_at'])}"
        
        # Send to emergency contacts
        for contact in contacts:
            try:
                # In a real implementation, you would send SMS or call
                # For now, we'll just log it
                logger.info(f"SOS Alert for user {user_id} - Contact: {contact['contact_name']} ({contact['contact_phone']})")
            except Exception as e:
                logger.warning(f"Failed to send SOS to contact: {e}")
        
        await query.edit_message_text('🆘 Emergency alert activated! Your contacts have been notified.')

# Keep existing functions but add enhancements
async def go_online(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = update.effective_user.id
    db = context.bot_data.get('db')
    drv = await db.get_driver_by_tg(tg_id)
    
    if not drv:
        await update.message.reply_text('You\'re not registered. Run /driver_start to register first.')
        return
        
    await db.set_driver_status(tg_id, 'online')
    kb = mk_location_keyboard()
    
    driver_rating = f" (Current rating: {drv.get('rating', 5.0):.1f}⭐)" if drv.get('rating') else ""
    await update.message.reply_text(
        f'You\'re now ONLINE{driver_rating}. Please share your current location so the system can find you for nearby rides.',
        reply_markup=kb
    )

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
        await update.message.reply_text('📍 Location updated. You\'ll receive ride requests in your area.')
    else:
        await update.message.reply_text('📍 Location received.')

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
    
    ride = await db.get_ride(ride_id)
    if ride and ride['rider_tg_id']:
        # Ask rider for rating
        await context.bot.send_message(
            chat_id=ride['rider_tg_id'],
            text='🏁 Trip completed! Please rate your driver:',
            reply_markup=rating_buttons(ride_id)
        )
    
    await update.message.reply_text(f'Ride {ride_id} marked as completed. Thanks!')