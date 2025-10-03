# ride_history.py
from telegram import Update
from telegram.ext import ContextTypes
from utils import format_rides

PAGE_SIZE = 5

async def ride_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    role = context.user_data.get("role", "rider")
    page = context.user_data.get("page", 0)

    db = context.bot_data["db"]
    rides = await db.get_ride_history(user_id, role, PAGE_SIZE, page * PAGE_SIZE)
    total = await db.count_ride_history(user_id, role)

    text = format_rides(rides, page, total, PAGE_SIZE, role)
    keyboard = []
    if page > 0:
        keyboard.append("/prev")
    if (page + 1) * PAGE_SIZE < total:
        keyboard.append("/next")

    await update.message.reply_text(text + "\n\n" + " ".join(keyboard))


async def next_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    page = context.user_data.get("page", 0)
    context.user_data["page"] = page + 1
    await ride_history(update, context)


async def prev_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    page = context.user_data.get("page", 0)
    if page > 0:
        context.user_data["page"] = page - 1
    await ride_history(update, context)
