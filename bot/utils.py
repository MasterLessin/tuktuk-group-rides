from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from typing import Optional

def mk_location_keyboard():
    kb = ReplyKeyboardMarkup([[KeyboardButton('Share Location', request_location=True)]], one_time_keyboard=True, resize_keyboard=True)
    return kb

def group_size_buttons():
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton('1-2', callback_data='group:1'),
         InlineKeyboardButton('3-4', callback_data='group:3'),
         InlineKeyboardButton('5+', callback_data='group:5')]
    ])
    return kb

def confirm_buttons():
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton('✅ Confirm', callback_data='confirm:yes'),
         InlineKeyboardButton('❌ Cancel', callback_data='confirm:no')]
    ])
    return kb

def accept_button_for_ride(ride_id: int):
    return InlineKeyboardMarkup([[InlineKeyboardButton('✅ Accept', callback_data=f'accept:{ride_id}')]])

def paginate_kb(page: int, total_pages: int):
    buttons = []
    if page > 1:
        buttons.append(InlineKeyboardButton('⬅ Prev', callback_data=f'history:{page-1}'))
    if page < total_pages:
        buttons.append(InlineKeyboardButton('Next ➡', callback_data=f'history:{page+1}'))
    if not buttons:
        return None
    return InlineKeyboardMarkup([buttons])
