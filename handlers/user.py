from aiogram import Router, types
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from database import get_db

router = Router()

# Το κεντρικό μόνιμο μενού του bot (Επιστροφή όλων των κουμπιών)
def get_main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🛍️ Catalog"), KeyboardButton(text="🖼️ Gallery")],
            [KeyboardButton(text="🛒 Cart"), KeyboardButton(text="💰 Wallet")],
            [KeyboardButton(text="🎟️ Promo Codes"), KeyboardButton(text="ℹ️ Info & Links")],
            [KeyboardButton(text="👤 Προφίλ")] # Αν είχες και κουμπί προφίλ ή επικοινωνίας
        ],
        resize_keyboard=True,
        is_persistent=True # Κρατάει το μενού πάντα ανοιχτό στο κάτω μέρος
    )

@router.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    # Κλείνουμε οποιαδήποτε "κολλημένη" διαδικασία (π.χ. αν κάποιος προσπαθούσε να κάνει εγγραφή πριν)
    await state.clear()
    
    user_id = message.from_user.id
    db_pool = await get_db()
    
    # Εγγράφουμε τον χρήστη κατευθείαν στη βάση. Στο Insta και TikTok βάζουμε 'Παραλείφθηκε'
    async with db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO users (telegram_id, instagram, tiktok) 
            VALUES ($1, 'Παραλείφθηκε', 'Παραλείφθηκε') 
            ON CONFLICT (telegram_id) DO NOTHING;
        """, user_id)
        
    welcome_text = (
        f"Γεια σου {message.from_user.first_name}! 💋\n\n"
        "Καλώς ήρθες στο επίσημο VIP Bot μου!\n\n"
        "Εδώ μπορείς να δεις όλο το αποκλειστικό μου υλικό, να μαζέψεις πόντους "
        "από τις αγορές σου και να ξεκλειδώσεις **δωρεάν** καυτά δώρα στην Gallery. 🔥\n\n"
        "Διάλεξε από το μενού παρακάτω τι θέλεις να κάνεις:"
    )
    
    await message.answer(welcome_text, reply_markup=get_main_menu(), parse_mode="Markdown")