from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from config import GROUP_LINK, ADMIN_LINK

def main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🛍️ Catalog"), KeyboardButton(text="🛒 Cart")],
            [KeyboardButton(text="👛 Wallet"), KeyboardButton(text="👤 My Profile")],
            [KeyboardButton(text="🎁 Giveaway"), KeyboardButton(text="🎟️ Promo Codes")],
            [KeyboardButton(text="🖼️ Gallery"), KeyboardButton(text="ℹ️ Info & Support")]
        ],
        resize_keyboard=True
    )

def support_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👥 Ομάδα ", url=GROUP_LINK)],
            [InlineKeyboardButton(text="💬 Προσωπικό Μήνυμα", url=ADMIN_LINK)]
        ]
    )