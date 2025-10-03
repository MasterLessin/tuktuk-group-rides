# handlers.py
from telegram.ext import CommandHandler, ConversationHandler, MessageHandler, filters
import ride_history

def register_handlers(app, db, admin_id):
    # share db/admin with handlers
    app.bot_data["db"] = db
    app.bot_data["admin_id"] = admin_id

    # basic commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("ridehistory", ride_history.ride_history))
    app.add_handler(CommandHandler("next", ride_history.next_page))
    app.add_handler(CommandHandler("prev", ride_history.prev_page))


async def start(update, context):
    await update.message.reply_text("Welcome to Thika TukTuk Bot 🚖\nUse /ridehistory to see your rides.")


async def help_cmd(update, context):
    await update.message.reply_text("Help menu:\n/start - Start bot\n/ridehistory - View rides")
