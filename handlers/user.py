import datetime
from aiogram import Router, F, types
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from database import get_db
from utils import get_user_level_info, get_level_progress

router = Router()

# Το κεντρικό μόνιμο μενού του bot
def get_main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🛍️ Catalog"), KeyboardButton(text="🖼️ Gallery")],
            [KeyboardButton(text="🛒 Cart"), KeyboardButton(text="💰 Wallet")],
            [KeyboardButton(text="🎟️ Promo Codes"), KeyboardButton(text="🎁 Giveaway")],
            [KeyboardButton(text="ℹ️ Info & Links"), KeyboardButton(text="👤 Προφίλ")]
        ],
        resize_keyboard=True,
        is_persistent=True # Κρατάει το μενού πάντα ανοιχτό στο κάτω μέρος
    )

@router.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    # Κλείνουμε οποιαδήποτε "κολλημένη" διαδικασία
    await state.clear()
    
    user_id = message.from_user.id
    db_pool = await get_db()
    
    # Εγγράφουμε τον χρήστη κατευθείαν στη βάση. Παραλείπουμε Insta και TikTok.
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

# --- ΛΕΙΤΟΥΡΓΙΕΣ ΤΩΝ ΝΕΩΝ ΚΟΥΜΠΙΩΝ ---

@router.message(F.text == "🎁 Giveaway")
async def show_giveaway(message: types.Message):
    db_pool = await get_db()
    async with db_pool.acquire() as conn:
        giveaway = await conn.fetchrow("SELECT ends_at, prizes FROM giveaway_settings WHERE id = 1;")
        user_data = await conn.fetchrow("SELECT giveaway_tickets FROM users WHERE telegram_id = $1;", message.from_user.id)
        tickets = user_data['giveaway_tickets'] if user_data else 0

    if not giveaway or not giveaway['ends_at'] or giveaway['ends_at'].year == 2000:
        await message.answer("Δεν τρέχει κάποια κλήρωση αυτή τη στιγμή. Μείνε συντονισμένος/η! 💋")
        return

    now = datetime.datetime.now()
    if now > giveaway['ends_at']:
        await message.answer("Η κλήρωση έχει λήξει! Αναμένουμε τα αποτελέσματα. 🎁")
        return

    time_left = giveaway['ends_at'] - now
    hours, remainder = divmod(int(time_left.total_seconds()), 3600)
    minutes, _ = divmod(remainder, 60)

    text = (
        "🚨 **MEGA VIP ΚΛΗΡΩΣΗ** 🚨\n\n"
        f"🎁 **Τα Δώρα μας:**\n{giveaway['prizes']}\n\n"
        f"⏳ **Λήγει σε:** {hours} ώρες και {minutes} λεπτά!\n\n"
        f"🎟️ **Τα δικά σου εισιτήρια:** {tickets}\n\n"
        "💡 _Υπενθύμιση: Κάθε 10€ αγορών σου δίνουν αυτόματα 1 επιπλέον εισιτήριο!_"
    )
    await message.answer(text, parse_mode="Markdown")

@router.message(F.text == "🎟️ Promo Codes")
async def show_promo_info(message: types.Message):
    text = (
        "🎟️ **Εκπτωτικοί Κωδικοί (Promo Codes)**\n\n"
        "Όταν έχεις κάποιον κωδικό προσφοράς, τον χρησιμοποιείς την ώρα που κάνεις την παραγγελία σου μέσα στο **Καλάθι (Cart)**! 🛒\n\n"
        "👉 Πάτα 'Cart' και θα δεις την επιλογή για εισαγωγή κωδικού.\n\n"
        "Μείνε συντονισμένος/η στο κανάλι για να προλαβαίνεις τα νέα Promo Codes που ανεβάζω! 💋"
    )
    await message.answer(text, parse_mode="Markdown")

@router.message(F.text == "ℹ️ Info & Links")
async def show_info_links(message: types.Message):
    text = (
        "ℹ️ **Χρήσιμοι Σύνδεσμοι & Πληροφορίες**\n\n"
        "Ό,τι χρειάζεσαι είναι εδώ:\n\n"
        "💬 **Επικοινωνία μαζί μου:** Στείλε μου DM για βοήθεια ή εξαργύρωση δώρων.\n"
        "📣 **Κανάλι Ενημερώσεων:** Εκεί ανεβάζω νέα teasers και κληρώσεις!\n"
        "⭐ **Premium VIP:** Η κλειστή μας ομάδα μόνο για συνδρομητές.\n\n"
        "*(Αν δεν έχεις τα links, στείλε μου προσωπικό μήνυμα να στα δώσω!)* 💋"
    )
    await message.answer(text, parse_mode="Markdown")

@router.message(F.text == "👤 Προφίλ")
async def show_profile(message: types.Message):
    user_id = message.from_user.id
    db_pool = await get_db()
    async with db_pool.acquire() as conn:
        user = await conn.fetchrow("SELECT balance, lifetime_points, total_spent, giveaway_tickets FROM users WHERE telegram_id = $1;", user_id)

    if not user:
        await message.answer("Δεν βρέθηκε προφίλ. Πάτα /start για να ξεκινήσεις!")
        return

    points = user['lifetime_points'] or 0
    current_title, _ = get_user_level_info(points)
    bar_string, next_level_string, _ = get_level_progress(points)

    text = (
        f"👤 **Το Προφίλ σου**\n\n"
        f"🎖️ **Level:** {current_title}\n"
        f"⭐ **Πόντοι:** {points}\n"
        f"{bar_string}\n"
        f"📈 _{next_level_string}_\n\n"
        f"💰 **Διαθέσιμο Υπόλοιπο:** {user['balance']}€\n"
        f"🛍️ **Συνολικά Έξοδα:** {user['total_spent']}€\n"
        f"🎟️ **Εισιτήρια Κλήρωσης:** {user['giveaway_tickets']}"
    )
    await message.answer(text, parse_mode="Markdown")