from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from typing import Optional
import math

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

def rating_buttons(ride_id: int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('⭐ 1', callback_data=f'rate:{ride_id}:1'),
         InlineKeyboardButton('⭐⭐ 2', callback_data=f'rate:{ride_id}:2'),
         InlineKeyboardButton('⭐⭐⭐ 3', callback_data=f'rate:{ride_id}:3')],
        [InlineKeyboardButton('⭐⭐⭐⭐ 4', callback_data=f'rate:{ride_id}:4'),
         InlineKeyboardButton('⭐⭐⭐⭐⭐ 5', callback_data=f'rate:{ride_id}:5')]
    ])

def trip_actions_buttons(ride_id: int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('📍 Share Live Location', callback_data=f'share_location:{ride_id}')],
        [InlineKeyboardButton('🆘 SOS Emergency', callback_data=f'sos:{ride_id}')],
        [InlineKeyboardButton('❌ Cancel Trip', callback_data=f'cancel_trip:{ride_id}')]
    ])

def driver_trip_buttons(ride_id: int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('✅ Arrived at Pickup', callback_data=f'arrived:{ride_id}')],
        [InlineKeyboardButton('🚗 Trip Started', callback_data=f'started:{ride_id}')],
        [InlineKeyboardButton('🏁 Trip Completed', callback_data=f'completed:{ride_id}')],
        [InlineKeyboardButton('❌ Cancel Trip', callback_data=f'cancel_driver:{ride_id}')]
    ])

def calculate_fare_estimate(distance_km: float, estimated_time_min: int, group_size: int) -> float:
    """Calculate fare estimate based on distance, time and group size"""
    base_fare = 100  # Base fare in local currency
    per_km_rate = 50  # Rate per km
    per_min_rate = 2  # Rate per minute
    group_surcharge = 1.0 + (group_size - 1) * 0.2  # 20% surcharge per additional person
    
    fare = (base_fare + (distance_km * per_km_rate) + (estimated_time_min * per_min_rate)) * group_surcharge
    return round(fare, 2)

def estimate_travel_time(distance_km: float, avg_speed_kmh: float = 30) -> int:
    """Estimate travel time in minutes"""
    return int((distance_km / avg_speed_kmh) * 60)