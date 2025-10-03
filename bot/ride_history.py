import math, time
from telegram import Update
from telegram.ext import ContextTypes
from .utils import paginate_kb, main_menu_keyboard

PAGE_SIZE = 5

async def my_rides_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await send_page(update, context, user_id, 1)

async def history_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data  # format "history:2"
    _, page_s = data.split(':')
    page = int(page_s)
    user_id = query.from_user.id
    await send_page(query, context, user_id, page)

async def send_page(target, context, user_id: int, page: int):
    db = context.bot_data.get('db')
    total = await db.count_rides_by_rider(user_id)
    total_pages = max(1, math.ceil(total / PAGE_SIZE))
    offset = (page - 1) * PAGE_SIZE
    rides = await db.get_rides_by_rider(user_id, limit=PAGE_SIZE, offset=offset)
    
    if not rides:
        text = '📋 You have no rides yet.'
        kb = main_menu_keyboard()
    else:
        lines = []
        for r in rides:
            created = ''
            if r.get('created_at'):
                created = f" — {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(r['created_at']))}"
            drop = ''
            if r.get('drop_lat') and r.get('drop_lng'):
                drop = f"Drop: ({r['drop_lat']:.5f}, {r['drop_lng']:.5f})"
            elif r.get('drop_text'):
                drop = f"Drop: {r['drop_text']}"
            lines.append(f"Ride #{r.get('id')}: Status: {r.get('status')} | Group: {r.get('group_size')} | Pickup: ({r['pickup_lat']:.5f}, {r['pickup_lng']:.5f}) | {drop}{created}")
        text = '\n\n'.join(lines)
        text = f'📋 Your Rides (Page {page}/{total_pages})\n\n' + text
        kb = paginate_kb(page, total_pages)
    
    if hasattr(target, 'message'):
        await target.message.reply_text(text, reply_markup=kb)
    else:
        await target.edit_message_text(text, reply_markup=kb)
