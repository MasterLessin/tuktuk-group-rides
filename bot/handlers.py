from telegram.ext import (
    CommandHandler, MessageHandler, CallbackQueryHandler, ConversationHandler, filters
)
from . import registration, rides, ride_history, admin, safety
from .safety import EMERGENCY_CONTACT_NAME, EMERGENCY_CONTACT_PHONE, add_emergency_contact_start, emergency_contact_name_received, emergency_contact_phone_received, view_emergency_contacts, share_trip_status

def register_handlers(app, db, admin_id):
    app.bot_data['db'] = db
    app.bot_data['admin_id'] = admin_id

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

    # emergency contact conversation
    emergency_conv = ConversationHandler(
        entry_points=[CommandHandler('add_emergency_contact', add_emergency_contact_start)],
        states={
            EMERGENCY_CONTACT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, emergency_contact_name_received)],
            EMERGENCY_CONTACT_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, emergency_contact_phone_received)],
        },
        fallbacks=[CommandHandler('cancel', lambda u,c: ConversationHandler.END)],
        per_message=False
    )
    app.add_handler(emergency_conv)

    # ride conversation
    ride_conv = ConversationHandler(
        entry_points=[CommandHandler('request', rides.request_start)],
        states={
            rides.PICKUP: [MessageHandler(filters.LOCATION, rides.pickup_received)],
            rides.DROP: [MessageHandler(filters.LOCATION | filters.Regex('^Skip$') | filters.TEXT, rides.drop_received)],
            rides.GROUP: [CallbackQueryHandler(rides.group_callback, pattern='^group:')],
            rides.CONFIRM: [CallbackQueryHandler(rides.confirm_callback, pattern='^confirm:')],
        },
        fallbacks=[CommandHandler('cancel', rides.cancel_conv)],
        per_message=False
    )
    app.add_handler(ride_conv)

    # basic commands & handlers
    app.add_handler(CommandHandler('start', lambda u,c: u.message.reply_text('Welcome to TukTuk Group Rides! Use /request to request a ride.')))
    app.add_handler(CommandHandler('help', lambda u,c: u.message.reply_text(
        'Use /request to request a ride, /driver_start to register as driver, /my_rides to view history.\n\n'
        'Safety Features:\n'
        '/add_emergency_contact - Add emergency contact\n'
        '/view_emergency_contacts - View your emergency contacts\n'
        '/share_trip <ride_id> <phone> - Share trip status\n\n'
        'Driver Commands:\n'
        '/go_online - Go online to receive rides\n'
        '/complete_ride <id> - Mark ride as completed'
    )))
    
    # Safety commands
    app.add_handler(CommandHandler('view_emergency_contacts', view_emergency_contacts))
    app.add_handler(CommandHandler('share_trip', share_trip_status))
    
    # Admin commands
    app.add_handler(CommandHandler('set_dispatch_group', admin.set_dispatch_group))
    app.add_handler(CommandHandler('broadcast', admin.broadcast))
    
    # Driver commands
    app.add_handler(CommandHandler('go_online', rides.go_online))
    app.add_handler(CommandHandler('complete_ride', rides.complete_ride_cmd))
    
    # Location handler
    app.add_handler(MessageHandler(filters.LOCATION, rides.location_handler))
    
    # Callback handlers
    app.add_handler(CallbackQueryHandler(rides.accept_callback, pattern='^accept:'))
    app.add_handler(CallbackQueryHandler(rides.cancel_trip_callback, pattern='^cancel_trip:'))
    app.add_handler(CallbackQueryHandler(rides.rate_trip_callback, pattern='^rate:'))
    app.add_handler(CallbackQueryHandler(rides.sos_callback, pattern='^sos:'))
    
    # Ride history
    app.add_handler(CommandHandler('my_rides', ride_history.my_rides_cmd))
    app.add_handler(CallbackQueryHandler(ride_history.history_callback, pattern='^history:'))