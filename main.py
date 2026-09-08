import asyncio
import datetime
import logging
import os
import aiohttp
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
import asyncpg

# --- CONFIGURATION ---
TOKEN = os.getenv("BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
DB_URL = os.getenv("DATABASE_URL", "postgresql://db_user:db_password@localhost:5432/db_name")
NOWPAYMENTS_API_KEY = os.getenv("NOWPAYMENTS_API_KEY", "")

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
        await connection.execute("""
            INSERT INTO giveaway_settings (id, ends_at) 
            VALUES (1, NOW() + INTERVAL '30 days') 
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
            [KeyboardButton(text="🛍️ Κατάλογος"), KeyboardButton(text="👛 Πορτοφόλι")],
            [KeyboardButton(text="👤 Το Προφίλ μου"), KeyboardButton(text="🎁 Κλήρωση")],
            [KeyboardButton(text="👥 Κοινότητα & Επικοινωνία"), KeyboardButton(text="ℹ️ Info")]
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

def topup_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💶 5€", callback_data="topup_5"), InlineKeyboardButton(text="💶 10€", callback_data="topup_10")],
            [InlineKeyboardButton(text="💶 20€", callback_data="topup_20"), InlineKeyboardButton(text="💶 50€", callback_data="topup_50")]
        ]
    )

# --- ADMIN HANDLERS ---
@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer(
        "👑 **Μενού Διαχειριστή**\n\n"
        "📜 `/add_media` - Ανέβασμα κλειδωμένου αρχείου\n"
        "💸 `/give_money <ID> <Ποσό>` - Πίστωση υπολοίπου\n"
        "⏳ `/set_giveaway <ώρες>` - Ορισμός λήξης κλήρωσης",
        parse_mode="Markdown"
    )

@dp.message(Command("set_giveaway"))
async def set_giveaway_timer(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    args = message.text.split()
    if len(args) != 2:
        await message.answer("⚠️ Χρήση: `/set_giveaway <ώρες>`", parse_mode="Markdown")
        return
    try:
        hours = float(args[1].replace(",", "."))
    except ValueError:
        await message.answer("❌ Μη έγκυρος αριθμός.")
        return

    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE giveaway_settings SET ends_at = NOW() + ($1 * INTERVAL '1 hour') WHERE id = 1;", hours)
        await conn.execute("UPDATE users SET giveaway_tickets = 0;")
    await message.answer(f"✅ Η κλήρωση ρυθμίστηκε να λήγει σε {hours} ώρες. Τα εισιτήρια μηδενίστηκαν!")

@dp.message(Command("give_money"))
async def admin_give_money(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    args = message.text.split()
    if len(args) != 3:
        await message.answer("⚠️ Χρήση: `/give_money <Telegram_ID> <Ποσό>`", parse_mode="Markdown")
        return
    try:
        target_id, amount = int(args[1]), float(args[2].replace(",", "."))
    except ValueError:
        await message.answer("❌ Λάθος μορφή.")
        return

    async with db_pool.acquire() as conn:
        user_exists = await conn.fetchval("SELECT 1 FROM users WHERE telegram_id = $1;", target_id)
        if not user_exists:
            await message.answer("❌ Ο χρήστης δεν βρέθηκε.")
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
    file_id = message.photo[-1].file_id if message.photo else message.video.file_id
    media_type = "photo" if message.photo else "video"
    await state.update_data(file_id=file_id, media_type=media_type)
    await message.answer("✍️ Γράψε την περιγραφή:")
    await state.set_state(UploadMedia.waiting_for_description)

@dp.message(UploadMedia.waiting_for_description, F.text)
async def receive_description(message: types.Message, state: FSMContext):
    await state.update_data(description=message.text)
    await message.answer("💰 Όρισε την τιμή (π.χ. 5.50):")
    await state.set_state(UploadMedia.waiting_for_price)

@dp.message(UploadMedia.waiting_for_price, F.text)
async def receive_price(message: types.Message, state: FSMContext):
    try:
        price = float(message.text.replace(",", "."))
    except ValueError:
        await message.answer("❌ Λάθος τιμή.")
        return
    
    data = await state.get_data()
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO locked_media (file_id, media_type, description, price) VALUES ($1, $2, $3, $4)",
            data['file_id'], data['media_type'], data['description'], price
        )
    await message.answer("✅ Το αρχείο ανέβηκε!")
    await state.clear()

# --- NOWPAYMENTS WALLET TOP-UP ---
@dp.message(F.text == "👛 Πορτοφόλι")
async def show_wallet(message: types.Message):
    async with db_pool.acquire() as conn:
        balance = await conn.fetchval("SELECT balance FROM users WHERE telegram_id = $1;", message.from_user.id)
    
    text = f"👛 **Το Πορτοφόλι μου**\n\nΔιαθέσιμο Υπόλοιπο: **{balance}€**\n\nΕπίλεξε ποσό κατάθεσης με κρυπτονομίσματα:"
    await message.answer(text, parse_mode="Markdown", reply_markup=topup_keyboard())

@dp.callback_query(F.data.startswith("topup_"))
async def process_crypto_topup(callback: CallbackQuery):
    if not NOWPAYMENTS_API_KEY:
        return await callback.answer("❌ Το NOWPayments API Key δεν έχει ρυθμιστεί στο Railway.", show_alert=True)
    
    amount = int(callback.data.split("_")[1])
    user_id = callback.from_user.id
    
    url = "https://api.nowpayments.io/v1/invoice"
    headers = {
        "x-api-key": NOWPAYMENTS_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "price_amount": amount,
        "price_currency": "eur",
        "pay_currency": "usdttrc20",
        "order_id": f"user_{user_id}_topup_{amount}",
        "order_description": f"Top-up {amount} EUR to bot wallet"
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=headers) as response:
            if response.status == 200 or response.status == 201:
                data = await response.json()
                invoice_url = data.get("invoice_url")
                
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text=f"💳 Πληρωμή {amount}€ με Crypto", url=invoice_url)]
                ])
                await callback.message.answer(
                    f"🔗 Δημιουργήθηκε ο σύνδεσμος πληρωμής για **{amount}€**.\n\n"
                    f"Πάτα το παρακάτω κουμπί για να ολοκληρώσεις την κατάθεση:",
                    reply_markup=keyboard,
                    parse_mode="Markdown"
                )
            else:
                await callback.message.answer("❌ Σφάλμα επικοινωνίας με το NOWPayments. Δοκιμάστε αργότερα.")
    
    await callback.answer()

# --- CATALOG & PURCHASE HANDLERS ---
@dp.message(F.text == "🛍️ Κατάλογος")
async def show_catalog(message: types.Message):
    async with db_pool.acquire() as conn:
        items = await conn.fetch("SELECT * FROM locked_media;")
    if not items:
        return await message.answer("Ο κατάλογος είναι άδειος!")
        
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
        settings = await conn.fetchrow("SELECT ends_at FROM giveaway_settings WHERE id = 1;")
        if settings and settings['ends_at'] <= datetime.datetime.now():
            await conn.execute("UPDATE users SET giveaway_tickets = 0;")
            await conn.execute("UPDATE giveaway_settings SET ends_at = NOW() + INTERVAL '30 days' WHERE id = 1;")

        user = await conn.fetchrow("SELECT balance, lifetime_points FROM users WHERE telegram_id = $1;", user_id)
        media = await conn.fetchrow("SELECT * FROM locked_media WHERE id = $1;", media_id)
        
        if not user or not media:
            return await callback.answer("Σφάλμα.", show_alert=True)
        if user['balance'] < media['price']:
            return await callback.answer("❌ Δεν έχεις αρκετό υπόλοιπο!", show_alert=True)
            
        new_balance = user['balance'] - media['price']
        points_earned = int(media['price']) 
        old_points = user['lifetime_points']
        tickets_to_add = ((old_points + points_earned) // 50) - (old_points // 50)

        async with conn.transaction():
            await conn.execute(
                "UPDATE users SET balance = $1, lifetime_points = lifetime_points + $2, giveaway_tickets = giveaway_tickets + $3, total_spent = total_spent + $4 WHERE telegram_id = $5;",
                new_balance, points_earned, tickets_to_add, media['price'], user_id
            )
            await conn.execute(
                "INSERT INTO purchases (telegram_id, items_summary, total_price) VALUES ($1, $2, $3);",
                user_id, f"Ξεκλείδωμα: {media['description']}", media['price']
            )
            
    await callback.answer("✅ Επιτυχής αγορά!", show_alert=False)
    if tickets_to_add > 0:
        try:
            await bot.send_message(user_id, f"🎉 **Συγχαρητήρια! Πήρες {tickets_to_add} εισιτήριο για την κλήρωση!**", parse_mode="Markdown")
        except Exception:
            pass
    
    if media['media_type'] == "photo":
        await bot.send_photo(chat_id=user_id, photo=media['file_id'], caption="🎉 Ορίστε το αρχείο!")
    else:
        await bot.send_video(chat_id=user_id, video=media['file_id'], caption="🎉 Ορίστε το αρχείο!")

# --- USER HANDLERS ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO users (telegram_id) VALUES ($1) ON CONFLICT (telegram_id) DO NOTHING;", message.from_user.id)
    await message.answer(f"Καλωσόρισες!\nΧρησιμοποίησε το μενού παρακάτω.", reply_markup=main_menu())

@dp.message(F.text == "👥 Κοινότητα & Επικοινωνία")
async def show_support(message: types.Message):
    await message.answer("Επικοινωνία & Ομάδα:", reply_markup=support_keyboard())

@dp.message(F.text == "👤 Το Προφίλ μου")
async def show_profile(message: types.Message):
    async with db_pool.acquire() as conn:
        user = await conn.fetchrow("SELECT * FROM users WHERE telegram_id = $1;", message.from_user.id)
        purchases = await conn.fetch("SELECT items_summary, total_price, created_at FROM purchases WHERE telegram_id = $1 ORDER BY created_at DESC LIMIT 5;", message.from_user.id)

    if not user:
        return await message.answer("Πληκτρολογήστε /start.")
    
    profile_text = (
        f"👤 **Προφίλ**\n\n"
        f"👛 **Υπόλοιπο:** {user['balance']}€\n"
        f"⭐ **Πόντοι:** {user['lifetime_points']}\n"
        f"🎖️ **Επίπεδο:** {get_user_level(user['lifetime_points'])}\n"
        f"🎟️ **Εισιτήρια:** {user['giveaway_tickets']}\n\n"
        f"📜 **Αγορές:**\n"
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
        settings = await conn.fetchrow("SELECT ends_at FROM giveaway_settings WHERE id = 1;")
        now = datetime.datetime.now()
        
        if settings and settings['ends_at'] <= now:
            await conn.execute("UPDATE users SET giveaway_tickets = 0;")
            await conn.execute("UPDATE giveaway_settings SET ends_at = NOW() + INTERVAL '30 days' WHERE id = 1;")
            settings = await conn.fetchrow("SELECT ends_at FROM giveaway_settings WHERE id = 1;")

        total_tickets = await conn.fetchval("SELECT SUM(giveaway_tickets) FROM users;") or 0
        hours, rem = divmod(int((settings['ends_at'] - now).total_seconds()), 3600)
        minutes = rem // 60

    await message.answer(f"🎁 **Κλήρωση**\n\n🎟️ **Συνολικά Εισιτήρια:** {total_tickets}\n⏳ **Λήξη σε:** {hours}ώ {minutes}λ\n\n💡 *50 πόντοι = 1 εισιτήριο!*", parse_mode="Markdown")

@dp.message(F.text == "ℹ️ Info")
async def show_info(message: types.Message):
    await message.answer("Όροι χρήσης.")

# --- MAIN EXECUTION ---
async def main():
    logging.basicConfig(level=logging.INFO)
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())