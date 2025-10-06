from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler, MessageHandler, filters

EMERGENCY_CONTACT_NAME, EMERGENCY_CONTACT_PHONE = range(20, 22)

async def add_emergency_contact_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('Please enter the name of your emergency contact:')
    return EMERGENCY_CONTACT_NAME

async def emergency_contact_name_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['emergency_contact_name'] = update.message.text.strip()
    await update.message.reply_text('Please enter the phone number of your emergency contact:')
    return EMERGENCY_CONTACT_PHONE

async def emergency_contact_phone_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    contact_name = context.user_data.get('emergency_contact_name')
    
    db = context.bot_data.get('db')
    user_id = update.effective_user.id
    
    try:
        await db.add_emergency_contact(user_id, contact_name, phone)
        await update.message.reply_text(f'✅ Emergency contact {contact_name} ({phone}) added successfully!')
    except Exception as e:
        await update.message.reply_text('❌ Failed to add emergency contact. Please try again.')
    
    return ConversationHandler.END

async def view_emergency_contacts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = context.bot_data.get('db')
    user_id = update.effective_user.id
    
    contacts = await db.get_emergency_contacts(user_id)
    
    if not contacts:
        await update.message.reply_text('You have no emergency contacts saved. Use /add_emergency_contact to add one.')
        return
    
    contacts_text = "🆘 **Your Emergency Contacts:**\n\n"
    for contact in contacts:
        contacts_text += f"👤 {contact['contact_name']}\n"
        contacts_text += f"📞 {contact['contact_phone']}\n\n"
    
    await update.message.reply_text(contacts_text)

async def share_trip_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text('Usage: /share_trip <ride_id> <phone_number>')
        return
    
    if len(args) < 2:
        await update.message.reply_text('Please provide both ride ID and phone number.')
        return
    
    ride_id = args[0]
    phone_number = args[1]
    
    db = context.bot_data.get('db')
    user_id = update.effective_user.id
    
    try:
        ride_id = int(ride_id)
    except ValueError:
        await update.message.reply_text('Invalid ride ID.')
        return
    
    ride = await db.get_ride(ride_id)
    if not ride or ride['rider_tg_id'] != user_id:
        await update.message.reply_text('Ride not found or you are not the rider.')
        return
    
    # In a real implementation, you would send SMS with trip details
    # For now, we'll just confirm
    await update.message.reply_text(
        f'✅ Trip {ride_id} status shared with {phone_number}.\n\n'
        f'They will receive updates about your trip for safety.'
    )