from telegram import Update
from telegram.ext import ConversationHandler, ContextTypes, MessageHandler, filters
from typing import Any

DRV_NAME, DRV_REG, DRV_PHONE = range(10,13)

async def driver_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('Driver registration — what is your full name?')
    return DRV_NAME

async def driver_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['drv_name'] = update.message.text.strip()
    await update.message.reply_text('Vehicle registration number (e.g., KBA 123A)?')
    return DRV_REG

async def driver_reg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['drv_reg'] = update.message.text.strip()
    await update.message.reply_text('Phone number (07xx...):')
    return DRV_PHONE

async def driver_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    tg_id = update.effective_user.id
    db = context.bot_data.get('db')
    try:
        await db.add_or_update_driver(tg_id, name=context.user_data.get('drv_name'), phone=phone, reg_no=context.user_data.get('drv_reg'))
        await update.message.reply_text('Thanks — you are registered. Use /go_online to set yourself online and share location.')
    except Exception:
        await update.message.reply_text('Failed to register. Try again later.')
    return ConversationHandler.END
