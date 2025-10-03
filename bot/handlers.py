from telegram.ext import (
    CommandHandler, MessageHandler, CallbackQueryHandler, ConversationHandler, filters
)
from telegram import Update
from telegram.ext import ContextTypes
from . import registration, rides, ride_history, admin
from .utils import main_menu_keyboard, driver_main_menu_keyboard, cancel_keyboard

# New handler functions
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command that goes straight to rider menu"""
    welcome_text = """
🚖 Welcome to TukTuk Group Rides!

You can:
- Request a group ride
- View your ride history
- Get help

Choose an option below:
    """
    await update.message.reply_text(welcome_text, reply_markup=main_menu_keyboard())

async def start_as_driver(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start as driver"""
    db = context.bot_data.get('db')
    user_id = update.effective_user.id
    
    # Check if driver is registered
    driver = await db.get_driver_by_tg(user_id)
    
    if driver:
        welcome_text = f"""
👨‍✈️ Welcome back, {driver.get('name', 'Driver')}!

You can:
- Go online to receive ride requests
- Update your location
- View your assigned jobs

Choose an option below:
        """
        await update.message.reply_text(welcome_text, reply_markup=driver_main_menu_keyboard())
    else:
        await update.message.reply_text(
            "👨‍✈️ To register as a driver, please use /driver_start command first.",
            reply_markup=main_menu_keyboard()
        )

async def back_to_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Return to main menu"""
    await start_command(update, context)

async def switch_to_rider(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Switch from driver to rider interface"""
    await start_command(update, context)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Help command"""
    help_text = """
🆘 **TukTuk Bot Help**

**For Riders:**
- 🚖 Request Ride: Book a tuktuk ride
- 📋 My Rides: View your ride history
- Share your location when prompted for accurate pickup

**For Drivers:**
- Register with /driver_start
- Go online to receive ride requests
- Update your location regularly

**Commands:**
/start - Show main menu
/help - This help message
/driver_start - Register as driver
    """
    await update.message.reply_text(help_text, reply_markup=main_menu_keyboard())

async def about_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """About command"""
    about_text = """
ℹ️ **About TukTuk Group Rides**

A digital tuktuk booking system that connects riders with registered drivers in real-time.

**Features:**
- Real-time ride requests
- Group ride options  
- Driver assignment system
- Ride history tracking

Safe, reliable, and convenient tuktuk services!
    """
    await update.message.reply_text(about_text, reply_markup=main_menu_keyboard())

async def request_location_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Request location update from driver"""
    from .utils import mk_location_keyboard
    await update.message.reply_text(
        "Please share your current location:",
        reply_markup=mk_location_keyboard()
    )

async def driver_jobs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show driver's assigned jobs"""
    await update.message.reply_text(
        "📊 Your assigned jobs will be shown here. Use /complete_ride <ride_id> when you finish a ride.",
        reply_markup=driver_main_menu_keyboard()
    )

def register_handlers(app, db, admin_id):
    app.bot_data['db'] = db
    app.bot_data['admin_id'] = admin_id

    # Start command - goes straight to rider menu
    app.add_handler(CommandHandler('start', start_command))
    app.add_handler(MessageHandler(filters.Regex(r'^⬅️ Back to Main Menu$'), back_to_main))
    app.add_handler(MessageHandler(filters.Regex(r'^👤 Switch to Rider$'), switch_to_rider))
    
    # Rider menu handlers
    app.add_handler(MessageHandler(filters.Regex(r'^🚖 Request Ride$'), rides.request_start))
    app.add_handler(MessageHandler(filters.Regex(r'^📋 My Rides$'), ride_history.my_rides_cmd))
    app.add_handler(MessageHandler(filters.Regex(r'^🆘 Help$'), help_command))
    app.add_handler(MessageHandler(filters.Regex(r'^ℹ️ About$'), about_command))
    
    # Driver access handler
    app.add_handler(MessageHandler(filters.Regex(r'^👨‍✈️ Driver Mode$'), start_as_driver))
    
    # Driver menu handlers  
    app.add_handler(MessageHandler(filters.Regex(r'^🟢 Go Online$'), rides.go_online))
    app.add_handler(MessageHandler(filters.Regex(r'^📍 Update Location$'), request_location_update))
    app.add_handler(MessageHandler(filters.Regex(r'^📊 My Jobs$'), driver_jobs))

    # registration conversation
    from .registration import DRV_NAME, DRV_REG, DRV_PHONE, driver_start, driver_name, driver_reg, driver_phone
    reg_conv = ConversationHandler(
        entry_points=[CommandHandler('driver_start', driver_start)],
        states={
            DRV_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, driver_name)],
            DRV_REG: [MessageHandler(filters.TEXT & ~filters.COMMAND, driver_reg)],
            DRV_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, driver_phone)],
        },
        fallbacks=[CommandHandler('cancel', lambda u,c: ConversationHandler.END)],
        per_message=False
    )
    app.add_handler(reg_conv)

    # ride conversation - UPDATED with proper filters
    ride_conv = ConversationHandler(
        entry_points=[
            CommandHandler('request', rides.request_start), 
            MessageHandler(filters.Regex(r'^🚖 Request Ride$'), rides.request_start)
        ],
        states={
            rides.PICKUP: [
                MessageHandler(filters.LOCATION, rides.pickup_received),
                MessageHandler(filters.TEXT & ~filters.COMMAND, rides.pickup_received)
            ],
            rides.DROP: [
                MessageHandler(filters.LOCATION, rides.drop_received),
                MessageHandler(filters.TEXT & ~filters.COMMAND, rides.drop_received)
            ],
            rides.GROUP: [CallbackQueryHandler(rides.group_callback, pattern='^group:')],
            rides.CONFIRM: [CallbackQueryHandler(rides.confirm_callback, pattern='^confirm:')],
        },
        fallbacks=[CommandHandler('cancel', rides.cancel_conv)],
        per_message=False
    )
    app.add_handler(ride_conv)

    # basic commands & handlers
    app.add_handler(CommandHandler('set_dispatch_group', admin.set_dispatch_group))
    app.add_handler(CommandHandler('broadcast', admin.broadcast))
    app.add_handler(CommandHandler('complete_ride', rides.complete_ride_cmd))
    
    # FIX: Remove the global location handler that was interfering with conversation
    # app.add_handler(MessageHandler(filters.LOCATION, rides.location_handler))  # REMOVED
    
    app.add_handler(CallbackQueryHandler(rides.accept_callback, pattern='^accept:'))
    app.add_handler(CallbackQueryHandler(ride_history.history_callback, pattern='^history:'))