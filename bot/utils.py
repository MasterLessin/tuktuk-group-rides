from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from typing import Optional

def main_menu_keyboard():
    """Main menu for riders"""
    kb = ReplyKeyboardMarkup([
        ["🚖 Request Ride", "📋 My Rides"],
        ["🆘 Help", "ℹ️ About"]
    ], resize_keyboard=True, one_time_keyboard=False)
    return kb

def driver_main_menu_keyboard():
    """Main menu for drivers"""
    kb = ReplyKeyboardMarkup([
        ["🟢 Go Online", "📍 Update Location"],
        ["📊 My Jobs", "🆘 Help"],
        ["👤 Switch to Rider"]
    ], resize_keyboard=True, one_time_keyboard=False)
    return kb

def rider_driver_choice_keyboard():
    """Choice between rider and driver"""
    kb = ReplyKeyboardMarkup([
        ["🚖 I'm a Rider", "👨‍✈️ I'm a Driver"]
    ], resize_keyboard=True, one_time_keyboard=True)
    return kb

def cancel_keyboard():
    """Cancel button for various flows"""
    kb = ReplyKeyboardMarkup([
        ["❌ Cancel"]
    ], resize_keyboard=True, one_time_keyboard=True)
    return kb

def back_to_main_keyboard():
    """Back to main menu"""
    kb = ReplyKeyboardMarkup([
        ["⬅️ Back to Main Menu"]
    ], resize_keyboard=True, one_time_keyboard=True)
    return kb

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
