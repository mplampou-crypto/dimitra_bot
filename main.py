import asyncio
import logging
import os
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
import asyncpg

# --- CONFIGURATION ---
TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"
DB_URL = "postgresql://db_user:db_password@localhost:5432/db_name"

# Links for Support button
GROUP_LINK = "https://t.me/your_group"
ADMIN_LINK = "https://t.me/your_username"

bot = Bot(token=TOKEN)
dp = Dispatcher()

# --- DATABASE SETUP ---
db_pool = None

async def init_db():
    global db_pool
    db_pool = await asyncpg.create_pool(DB_URL)
    async with db_pool.acquire() as connection:
        # Users Table
        await connection.execute("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_id BIGINT PRIMARY KEY,
                balance NUMERIC(10, 2) DEFAULT 0.00,
                lifetime_points INT DEFAULT 0,
                total_spent NUMERIC(10, 2) DEFAULT 0.00,
                created_at TIMESTAMP DEFAULT NOW()
            );
        """)
        # Purchases History Table
        await connection.execute("""
            CREATE TABLE IF NOT EXISTS purchases (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT,
                items_summary TEXT,
                total_price NUMERIC(10, 2),
                created_at TIMESTAMP DEFAULT NOW()
            );
        """)

# --- HELPER FUNCTIONS ---
def get_user_level(points: int) -> str:
    """Υπολογισμός επιπέδου βάσει πόντων (όρια: 150, 300, 500, 1000)"""
    if points >= 1000:
        return "💎 Diamond (1000+ πόντοι)"  # Μπορείς να αλλάξεις τα ονόματα εδώ
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

# --- HANDLERS ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (telegram_id) VALUES ($1) ON CONFLICT (telegram_id) DO NOTHING;",
            user_id
        )
    await message.answer(
        f"Καλωσόρισες, {message.from_user.first_name}!\nΧρησιμοποίησε το μενού παρακάτω για να πλοηγηθείς στο bot.",
        reply_markup=main_menu()
    )

@dp.message(F.text == "👥 Κοινότητα & Επικοινωνία")
async def show_support(message: types.Message):
    await message.answer(
        "Μπορείς να συνδεθείς στην κοινότητά μας ή να επικοινωνήσεις απεχευθείας μαζί μας παρακάτω:",
        reply_markup=support_keyboard()
    )

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
        await message.answer("Δεν βρέθηκαν στοιχεία προφίλ. Πληκτρολογήστε /start.")
        return

    balance = user['balance']
    points = user['lifetime_points']
    level_title = get_user_level(points)
    tickets = points // 50  # 1 εισιτήριο ανά 50 πόντους

    profile_text = (
        f"👤 **Το Προφίλ σου**\n\n"
        f"👛 **Υπόλοιπο:** {balance}€\n"
        f"⭐ **Πόντοι:** {points}\n"
        f"🎖️ **Τίτλος / Επίπεδο:** {level_title}\n"
        f"🎟️ **Εισιτήρια Κλήρωσης:** {tickets} (1 ανά 50 πόντους)\n\n"
        f"📜 **Πρόσφατες Αγορές:**\n"
    )

    if purchases:
        for p in purchases:
            profile_text += f"- {p['items_summary']} ({p['total_price']}€) στις {p['created_at'].strftime('%d/%m %H:%M')}\n"
    else:
        profile_text += "Δεν έχεις κάνει κάποια αγορά ακόμα."

    await message.answer(profile_text, parse_mode="Markdown")

@dp.message(F.text == "🎁 Κλήρωση")
async def show_giveaway(message: types.Message):
    async with db_pool.acquire() as conn:
        # Υπολογισμός συνολικών πόντων όλων των χρηστών για να δούμε πόσα συνολικά εισιτήρια έχουν δοθεί
        total_points = await conn.fetchval("SELECT SUM(lifetime_points) FROM users;") or 0
        total_tickets = total_points // 50

    text = (
        f"🎁 **Μηνιαία Μεγάλη Κλήρωση**\n\n"
        f"🎟️ **Συνολικά Εισιτήρια που έχουν δοθεί:** {total_tickets}\n"
        f"⏳ **Λήξη Κλήρωσης:** Τέλος του τρέχοντος μηνός!\n\n"
        f"💡 *Κάθε 50 πόντοι (10€ αγορών = 10 πόντοι) σου εξασφαλίζουν αυτόματα 1 εισιτήριο για την κλήρωση!*"
    )
    await message.answer(text, parse_mode="Markdown")

@dp.message(F.text == "ℹ️ Info")
async def show_info(message: types.Message):
    await message.answer("Εδώ μπορείς να προσθέσεις πληροφορίες για σένα, την επιχείρησή σου ή τους όρους χρήσης.")

# --- MAIN FUNCTION ---
async def main():
    logging.basicConfig(level=logging.INFO)
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())