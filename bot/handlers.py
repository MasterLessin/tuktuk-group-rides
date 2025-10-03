from telegram.ext import (
    CommandHandler, MessageHandler, CallbackQueryHandler, ConversationHandler, filters
)
from . import registration, rides, ride_history, admin

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

    # ride conversation
    ride_conv = ConversationHandler(
        entry_points=[CommandHandler('request', rides.request_start), MessageHandler(filters.Regex(r'^Request Group Ride$'), rides.request_start)],
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
    app.add_handler(CommandHandler('help', lambda u,c: u.message.reply_text('Use /request to request a ride, /driver_start to register as driver, /my_rides to view history.')))
    app.add_handler(CommandHandler('set_dispatch_group', admin.set_dispatch_group))
    app.add_handler(CommandHandler('broadcast', admin.broadcast))
    app.add_handler(CommandHandler('go_online', rides.go_online))
    app.add_handler(CommandHandler('complete_ride', rides.complete_ride_cmd))
    app.add_handler(MessageHandler(filters.LOCATION, rides.location_handler))
    app.add_handler(CallbackQueryHandler(rides.accept_callback, pattern='^accept:'))
    app.add_handler(CommandHandler('my_rides', ride_history.my_rides_cmd))
    app.add_handler(CallbackQueryHandler(ride_history.history_callback, pattern='^history:'))
