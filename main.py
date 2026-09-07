import asyncio
import datetime
import logging
import os
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
import asyncpg

# --- CONFIGURATION ---
TOKEN = os.getenv("BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
DB_URL = os.getenv("DATABASE_URL", "postgresql://db_user:db_password@localhost:5432/db_name")

ADMIN_ID = int(os.getenv("ADMIN_ID", 0))

GROUP_LINK = "https://t.me/your_group"
ADMIN_LINK = "https://t.me/your_username"

bot = Bot(token=TOKEN)
dp = Dispatcher()
db_pool = None

# --- FSM STATES FOR ADMIN ---
class UploadMedia(StatesGroup):
    waiting_for_file = State()
    waiting_for_description = State()
    waiting_for_price = State()

# --- DATABASE SETUP ---
async def init_db():
    global db_pool
    db_pool = await asyncpg.create_pool(DB_URL)
    async with db_pool.acquire() as connection:
        await connection.execute("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_id BIGINT PRIMARY KEY,
                balance NUMERIC(10, 2) DEFAULT 0.00,
                lifetime_points INT DEFAULT 0,
                giveaway_tickets INT DEFAULT 0,
                total_spent NUMERIC(10, 2) DEFAULT 0.00,
                created_at TIMESTAMP DEFAULT NOW()
            );
        """)
        await connection.execute("""
            CREATE TABLE IF NOT EXISTS purchases (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT,
                items_summary TEXT,
                total_price NUMERIC(10, 2),
                created_at TIMESTAMP DEFAULT NOW()
            );
        """)
        await connection.execute("""
            CREATE TABLE IF NOT EXISTS locked_media (
                id SERIAL PRIMARY KEY,
                file_id TEXT NOT NULL,
                media_type TEXT NOT NULL,
                description TEXT,
                price NUMERIC(10, 2) NOT NULL
            );
        """)
        await connection.execute("""
            CREATE TABLE IF NOT EXISTS giveaway_settings (
                id INT PRIMARY KEY,
                ends_at TIMESTAMP
            );
        """)
        # Εισαγωγή προεπιλεγμένης γραμμής αν δεν υπάρχει
        await connection.execute("""
            INSERT INTO giveaway_settings (id, ends_at) 
            VALUES (1, NOW() + INTERVAL '24 hours') 
            ON CONFLICT (id) DO NOTHING;
        """)

# --- HELPER FUNCTIONS ---
def get_user_level(points: int) -> str:
    if points >= 1000:
        return "💎 Diamond (1000+ πόντοι)"
    elif points >= 500:
        return "🥇 Gold (500+ πόντοι)"
    elif points >= 300:
        return "🥈 Silver (300+ πόντοι)"
    elif points >= 150:
        return "🥉 Bronze (150+ πόντοι)"
    else:
        return "🌱 Newcomer (<150 πόντοι)"

# --- KEYBOARDS ---
def main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🛍️ Κατάλογος"), KeyboardButton(text="🛒 Καλάθι")],
            [KeyboardButton(text="👛 Πορτοφόλι"), KeyboardButton(text="👤 Το Προφίλ μου")],
            [KeyboardButton(text="🎁 Κλήρωση"), KeyboardButton(text="👥 Κοινότητα & Επικοινωνία")],
            [KeyboardButton(text="ℹ️ Info")]
        ],
        resize_keyboard=True
    )

def support_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👥 Ομάδα / Κανάλι", url=GROUP_LINK)],
            [InlineKeyboardButton(text="💬 Προσωπικό Μήνυμα", url=ADMIN_LINK)]
        ]
    )

# --- ADMIN HANDLERS ---
@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    
    await message.answer(
        "👑 Καλωσόρισες στο κρυφό μενού διαχειριστή!\n\n"
        "📜 /add_media - Ανέβασμα κλειδωμένου αρχείου\n"
        "💸 /give_money <ID> <Ποσό> - Πιστώσεις υπολοίπου\n"
        "⏳ /set_giveaway <ώρες> - Ορισμός διάρκειας κλήρωσης (π.χ. /set_giveaway 5)"
    )

@dp.message(Command("set_giveaway"))
async def set_giveaway_timer(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    args = message.text.split()
    if len(args) != 2:
        await message.answer("⚠️ Χρήση: `/set_giveaway <ώρες>` (π.χ. `/set_giveaway 5`)", parse_mode="Markdown")
        return

    try:
        hours = float(args[1].replace(",", "."))
    except ValueError:
        await message.answer("❌ Δώσε έναν έγκυρο αριθμό ωρών (π.χ. 5 ή 2.5).")
        return

    async with db_pool.acquire() as conn:
        # Υπολογισμός νέου χρόνου λήξης και μηδενισμός εισιτηρίων παλαιότερης κλήρωσης
        await conn.execute(
            "UPDATE giveaway_settings SET ends_at = NOW() + ($1 * INTERVAL '1 hour') WHERE id = 1;",
            hours
        )
        await conn.execute("UPDATE users SET giveaway_tickets = 0;")

    await message.answer(f"✅ Η κλήρωση ρυθμίστηκε να λήγει σε {hours} ώρες και τα εισιτήρια μηδενίστηκαν!")

@dp.message(Command("give_money"))
async def admin_give_money(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    args = message.text.split()
    if len(args) != 3:
        await message.answer("⚠️ Χρήση: `/give_money <Telegram_ID> <Ποσό>`", parse_mode="Markdown")
        return

    try:
        target_id = int(args[1])
        amount = float(args[2].replace(",", "."))
    except ValueError:
        await message.answer("❌ Λάθος μορφή αριθμών.")
        return

    async with db_pool.acquire() as conn:
        user_exists = await conn.fetchval("SELECT 1 FROM users WHERE telegram_id = $1;", target_id)
        if not user_exists:
            await message.answer("❌ Ο χρήστης δεν βρέθηκε (πρέπει να έχει πατήσει /start).")
            return

        await conn.execute("UPDATE users SET balance = balance + $1 WHERE telegram_id = $2;", amount, target_id)

    await message.answer(f"✅ Πιστώθηκαν {amount}€ στον χρήστη {target_id}!")
    try:
        await bot.send_message(target_id, f"🎉 Το υπόλοιπό σου πιστώθηκε με {amount}€ από τον διαχειριστή!")
    except Exception:
        pass

@dp.message(Command("add_media"))
async def start_upload(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer("📤 Στείλε μου τη φωτογραφία ή το βίντεο που θες να κλειδώσουμε:")
    await state.set_state(UploadMedia.waiting_for_file)

@dp.message(UploadMedia.waiting_for_file, F.photo | F.video)
async def receive_media(message: types.Message, state: FSMContext):
    if message.photo:
        file_id = message.photo[-1].file_id
        media_type = "photo"
    else:
        file_id = message.video.file_id
        media_type = "video"
        
    await state.update_data(file_id=file_id, media_type=media_type)
    await message.answer("✍️ Γράψε τώρα την περιγραφή:")
    await state.set_state(UploadMedia.waiting_for_description)

@dp.message(UploadMedia.waiting_for_description, F.text)
async def receive_description(message: types.Message, state: FSMContext):
    await state.update_data(description=message.text)
    await message.answer("💰 Όρισε την τιμή ξεκλειδώματος σε ευρώ:")
    await state.set_state(UploadMedia.waiting_for_price)

@dp.message(UploadMedia.waiting_for_price, F.text)
async def receive_price(message: types.Message, state: FSMContext):
    try:
        price = float(message.text.replace(",", "."))
    except ValueError:
        await message.answer("❌ Δώσε έγκυρη τιμή.")
        return

    data = await state.get_data()
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO locked_media (file_id, media_type, description, price) VALUES ($1, $2, $3, $4)",
            data['file_id'], data['media_type'], data['description'], price
        )
        
    await message.answer("✅ Το αρχείο ανέβηκε επιτυχώς!")
    await state.clear()

# --- CATALOG & PURCHASE HANDLERS ---
@dp.message(F.text == "🛍️ Κατάλογος")
async def show_catalog(message: types.Message):
    async with db_pool.acquire() as conn:
        items = await conn.fetch("SELECT * FROM locked_media;")
        
    if not items:
        await message.answer("Ο κατάλογος είναι άδειος προς το παρόν!")
        return
        
    for item in items:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"🔓 Ξεκλείδωμα ({item['price']}€)", callback_data=f"buy_{item['id']}")]
        ])
        await message.answer(f"🔒 **Κλειδωμένο Αρχείο**\n\n📝 {item['description']}", reply_markup=keyboard)

@dp.callback_query(F.data.startswith("buy_"))
async def process_purchase(callback: CallbackQuery):
    media_id = int(callback.data.split("_")[1])
    user_id = callback.from_user.id
    
    async with db_pool.acquire() as conn:
        # Έλεγχος λήξης κλήρωσης πριν την αγορά
        settings = await conn.fetchrow("SELECT ends_at FROM giveaway_settings WHERE id = 1;")
        if settings and settings['ends_at'] <= datetime.datetime.now():
            # Λήξη χρόνου: Μηδενισμός εισιτηρίων και ανανέωση χρόνου για επόμενο μήνα/κύκλο
            await conn.execute("UPDATE users SET giveaway_tickets = 0;")
            await conn.execute("UPDATE giveaway_settings SET ends_at = NOW() + INTERVAL '30 days' WHERE id = 1;")

        user = await conn.fetchrow("SELECT balance, lifetime_points FROM users WHERE telegram_id = $1;", user_id)
        media = await conn.fetchrow("SELECT * FROM locked_media WHERE id = $1;", media_id)
        
        if not user or not media:
            await callback.answer("Σφάλμα συστήματος.", show_alert=True)
            return
            
        if user['balance'] < media['price']:
            await callback.answer("❌ Ανεπαρκές υπόλοιπο!", show_alert=True)
            return
            
        new_balance = user['balance'] - media['price']
        points_earned = int(media['price']) # 1€ = 1 πόντος
        new_total_points = user['lifetime_points'] + points_earned
        
        # Υπολογισμός αυτόματης μετατροπής πόντων σε εισιτήρια (1 εισιτήριο ανά 50 πόντους)
        tickets_to_add = new_total_points // 50 - user['lifetime_points'] // 50

        async with conn.transaction():
            await conn.execute(
                """UPDATE users 
                   SET balance = $1, 
                       lifetime_points = lifetime_points + $2, 
                       giveaway_tickets = giveaway_tickets + $3, 
                       total_spent = total_spent + $4 
                   WHERE telegram_id = $5;""",
                new_balance, points_earned, tickets_to_add, media['price'], user_id
            )
            await conn.execute(
                "INSERT INTO purchases (telegram_id, items_summary, total_price) VALUES ($1, $2, $3);",
                user_id, f"Ξεκλείδωμα αρχείου: {media['description']}", media['price']
            )

    await callback.answer("✅ Επιτυχής αγορά!", show_alert=False)

    # Ενημέρωση χρήστη αν κέρδισε εισιτήριο
    if tickets_to_add > 0:
        try:
            await bot.send_message(user_id, f"🎟️ **Συγχαρητήρια!** Συγκέντρωσες 50 πόντους και κέρδισες **{tickets_to_add} εισιτήριο** για την κλήρωση!", parse_mode="Markdown")
        except Exception:
            pass
    
    if media['media_type'] == "photo":
        await bot.send_photo(chat_id=user_id, photo=media['file_id'], caption="🎉 Ορίστε το αρχείο σου!")
    elif media['media_type'] == "video":
        await bot.send_video(chat_id=user_id, video=media['file_id'], caption="🎉 Ορίστε το αρχείο σου!")

# --- USER HANDLERS ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (telegram_id) VALUES ($1) ON CONFLICT (telegram_id) DO NOTHING;",
            user_id
        )
    await message.answer(
        f"Καλωσόρισες, {message.from_user.first_name}!\nΧρησιμοποίησε το μενού παρακάτω.",
        reply_markup=main_menu()
    )

@dp.message(F.text == "👥 Κοινότητα & Επικοινωνία")
async def show_support(message: types.Message):
    await message.answer("Επικοινωνία & Ομάδα:", reply_markup=support_keyboard())

@dp.message(F.text == "👤 Το Προφίλ μου")
async def show_profile(message: types.Message):
    user_id = message.from_user.id
    async with db_pool.acquire() as conn:
        user = await conn.fetchrow("SELECT * FROM users WHERE telegram_id = $1;", user_id)
        purchases = await conn.fetch(
            "SELECT items_summary, total_price, created_at FROM purchases WHERE telegram_id = $1 ORDER BY created_at DESC LIMIT 5;",
            user_id
        )

    if not user:
        await message.answer("Πληκτρολογήστε /start.")
        return

    balance = user['balance']
    points = user['lifetime_points']
    tickets = user['giveaway_tickets']
    level_title = get_user_level(points)

    profile_text = (
        f"👤 **Το Προφίλ σου**\n\n"
        f"👛 **Υπόλοιπο:** {balance}€\n"
        f"⭐ **Πόντοι:** {points}\n"
        f"🎖️ **Επίπεδο:** {level_title}\n"
        f"🎟️ **Εισιτήρια Κλήρωσης:** {tickets}\n\n"
        f"📜 **Πρόσφατες Αγορές:**\n"
    )

    if purchases:
        for p in purchases:
            profile_text += f"- {p['items_summary']} ({p['total_price']}€)\n"
    else:
        profile_text += "Καμία αγορά ακόμα."

    await message.answer(profile_text, parse_mode="Markdown")

@dp.message(F.text == "🎁 Κλήρωση")
async def show_giveaway(message: types.Message):
    async with db_pool.acquire() as conn:
        # Έλεγχος αν έληξε ο χρόνος
        settings = await conn.fetchrow("SELECT ends_at FROM giveaway_settings WHERE id = 1;")
        now = datetime.datetime.now()
        
        if settings and settings['ends_at'] <= now:
            # Μηδενισμός εισιτηρίων αυτόματα
            await conn.execute("UPDATE users SET giveaway_tickets = 0;")
            await conn.execute("UPDATE giveaway_settings SET ends_at = NOW() + INTERVAL '30 days' WHERE id = 1;")
            settings = await conn.fetchrow("SELECT ends_at FROM giveaway_settings WHERE id = 1;")

        total_tickets = await conn.fetchval("SELECT SUM(giveaway_tickets) FROM users;") or 0
        
        # Υπολογισμός αντίστροφης μέτρησης
        remaining_time = settings['ends_at'] - now
        hours, remainder = divmod(int(remaining_time.total_seconds()), 3600)
        minutes, _ = divmod(remainder, 60)

    text = (
        f"🎁 **Μηνιαία Μεγάλη Κλήρωση**\n\n"
        f"🎟️ **Συνολικά Εισιτήρια στην κλήρωση:** {total_tickets}\n"
        f"⏳ **Χρόνος που απομένει:** {hours} ώρες και {minutes} λεπτά!\n\n"
        f"💡 *Κάθε 50 πόντοι από αγορές μετατρέπονται αυτόματα σε 1 εισιτήριο!*"
    )
    await message.answer(text, parse_mode="Markdown")

@dp.message(F.text == "ℹ️ Info")
async def show_info(message: types.Message):
    await message.answer("Πληροφορίες καταστήματος / όροι χρήσης.")

# --- MAIN EXECUTION ---
async def main():
    logging.basicConfig(level=logging.INFO)
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())