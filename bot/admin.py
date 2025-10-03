import os
from telegram import Update
from telegram.ext import ContextTypes

ADMIN_ID = None
try:
    ADMIN_ID = int(os.environ.get('ADMIN_ID', '0'))
except Exception:
    ADMIN_ID = None

async def set_dispatch_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != ADMIN_ID:
        await update.message.reply_text('Only the admin can set the dispatch group.')
        return
    chat_id = update.effective_chat.id
    db = context.bot_data.get('db')
    await db.set_setting('dispatch_chat_id', str(chat_id))
    await update.message.reply_text(f'Dispatch group saved (chat_id: {chat_id}). Drivers will receive ride requests here.')

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != ADMIN_ID:
        await update.message.reply_text('Only the admin can broadcast.')
        return
    text = ' '.join(context.args)
    if not text:
        await update.message.reply_text('Usage: /broadcast <message>')
        return
    db = context.bot_data.get('db')
    async with db.pool.acquire() as conn:
        rows = await conn.fetch('SELECT telegram_id FROM drivers;')
    count = 0
    for r in rows:
        try:
            await context.bot.send_message(chat_id=r['telegram_id'], text=f'📢 Broadcast from admin:\n\n{text}')
            count += 1
        except Exception:
            pass
    await update.message.reply_text(f'Broadcast sent to {count} drivers.')
