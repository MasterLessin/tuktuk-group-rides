from telegram import Update
from telegram.ext import ConversationHandler, ContextTypes, MessageHandler, filters
from typing import Any
from .utils import driver_main_menu_keyboard, cancel_keyboard, main_menu_keyboard

DRV_NAME, DRV_REG, DRV_PHONE = range(10,13)

async def driver_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        '👨‍✈️ Driver Registration\n\nWhat is your full name?',
        reply_markup=cancel_keyboard()
    )
    return DRV_NAME

async def driver_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == '❌ Cancel':
        await update.message.reply_text('Registration cancelled.', reply_markup=main_menu_keyboard())
        return ConversationHandler.END
        
    context.user_data['drv_name'] = update.message.text.strip()
    await update.message.reply_text(
        '🚗 Vehicle registration number (e.g., KBA 123A)?',
        reply_markup=cancel_keyboard()
    )
    return DRV_REG

async def driver_reg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == '❌ Cancel':
        await update.message.reply_text('Registration cancelled.', reply_markup=main_menu_keyboard())
        return ConversationHandler.END
        
    context.user_data['drv_reg'] = update.message.text.strip()
    await update.message.reply_text(
        '📞 Phone number (07xx...):',
        reply_markup=cancel_keyboard()
    )
    return DRV_PHONE

async def driver_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == '❌ Cancel':
        await update.message.reply_text('Registration cancelled.', reply_markup=main_menu_keyboard())
        return ConversationHandler.END
        
    phone = update.message.text.strip()
    tg_id = update.effective_user.id
    db = context.bot_data.get('db')
    try:
        await db.add_or_update_driver(
            tg_id, 
            name=context.user_data.get('drv_name'), 
            phone=phone, 
            reg_no=context.user_data.get('drv_reg')
        )
        success_text = f"""
✅ Registration Successful!

Welcome, {context.user_data.get('drv_name')}!
Vehicle: {context.user_data.get('drv_reg')}
Phone: {phone}

You can now go online to receive ride requests!
        """
        await update.message.reply_text(success_text, reply_markup=driver_main_menu_keyboard())
    except Exception as e:
        await update.message.reply_text(
            '❌ Failed to register. Please try again later.',
            reply_markup=main_menu_keyboard()
        )
    return ConversationHandler.END
