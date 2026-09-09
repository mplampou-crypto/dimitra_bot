import asyncio
import datetime
import logging
import os
import aiohttp
from decimal import Decimal
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, InputMediaPhoto, InputMediaVideo
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
    waiting_for_files = State()
    waiting_for_description = State()
    waiting_for_price = State()

# --- FSM STATES FOR PAYSAFE ---
class PaySafeTopUp(StatesGroup):
    waiting_for_amount = State()
    waiting_for_code = State()

# --- FSM STATES FOR AUTOMATED CRYPTO (NOWPAYMENTS) ---
class CryptoTopUp(StatesGroup):
    waiting_for_amount = State()

# --- FSM STATES FOR PROMO CODES ---
class CartPromo(StatesGroup):
    waiting_for_promo = State()

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
                file_ids TEXT[] NOT NULL,
                media_types TEXT[] NOT NULL,
                description TEXT,
                price NUMERIC(10, 2) NOT NULL
            );
        """)
        
        await connection.execute("""
            CREATE TABLE IF NOT EXISTS cart (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT,
                media_id INT,
                added_at TIMESTAMP DEFAULT NOW()
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
            VALUES (1, '2000-01-01 00:00:00') 
            ON CONFLICT (id) DO NOTHING;
        """)

        await connection.execute("""
            CREATE TABLE IF NOT EXISTS promo_codes (
                code TEXT PRIMARY KEY,
                discount INT NOT NULL,
                bonus_tickets INT DEFAULT 0,
                expires_at TIMESTAMP
            );
        """)

        await connection.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS lifetime_points INT DEFAULT 0;")
        await connection.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS giveaway_tickets INT DEFAULT 0;")
        await connection.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS total_spent NUMERIC(10, 2) DEFAULT 0.00;")
        
        await connection.execute("ALTER TABLE promo_codes ADD COLUMN IF NOT EXISTS bonus_tickets INT DEFAULT 0;")
        await connection.execute("ALTER TABLE promo_codes ADD COLUMN IF NOT EXISTS expires_at TIMESTAMP;")
        # Σε περίπτωση που υπήρχαν ήδη παλιοί κωδικοί, τους βάζουμε λήξη σε 30 μέρες για να μην κρασάρουν
        await connection.execute("UPDATE promo_codes SET expires_at = NOW() + INTERVAL '30 days' WHERE expires_at IS NULL;")
        
        await connection.execute("ALTER TABLE locked_media ADD COLUMN IF NOT EXISTS file_ids TEXT[];")
        await connection.execute("ALTER TABLE locked_media ADD COLUMN IF NOT EXISTS media_types TEXT[];")
        
        await connection.execute("ALTER TABLE locked_media DROP COLUMN IF EXISTS file_id;")
        await connection.execute("ALTER TABLE locked_media DROP COLUMN IF EXISTS media_type;")

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

async def get_cart_text_and_keyboard(user_id: int, state: FSMContext):
    async with db_pool.acquire() as conn:
        cart_items = await conn.fetch("""
            SELECT c.id as cart_id, m.id as media_id, m.description, m.price 
            FROM cart c
            JOIN locked_media m ON c.media_id = m.id
            WHERE c.telegram_id = $1;
        """, user_id)
        
    if not cart_items:
        return "🛒 Το καλάθι σου είναι άδειο!", None
        
    original_price = sum(Decimal(str(item['price'])) for item in cart_items)
    
    data = await state.get_data()
    promo_discount = data.get("promo_discount", 0)
    promo_tickets = data.get("promo_tickets", 0)
    promo_code = data.get("promo_code", "")
    
    total_price = original_price
    if promo_discount > 0:
        multiplier = Decimal(str(1 - promo_discount / 100.0))
        total_price = (original_price * multiplier).quantize(Decimal('0.01'))
        
    text = "🛒 **Το Καλάθι σου:**\n\n"
    for item in cart_items:
        text += f"🔹 {item['description']} - **{item['price']}€**\n"
        
    if promo_discount > 0 or promo_tickets > 0:
        text += f"\n💰 **Αρχικό Ποσό:** {original_price:.2f}€\n"
        text += f"🎟️ **Έκπτωση ({promo_code}):** -{promo_discount}%\n"
        if promo_tickets > 0:
            text += f"🎁 **Extra Εισιτήρια Κλήρωσης:** +{promo_tickets}\n"
        text += f"🔥 **Τελικό Ποσό:** {total_price:.2f}€\n\n"
    else:
        text += f"\n💰 **Συνολικό Ποσό:** {total_price:.2f}€\n\n"
        
    text += "Μπορείς να αφαιρέσεις προϊόντα με τα παρακάτω κουμπιά:"
    
    inline_keyboard = []
    for item in cart_items:
        inline_keyboard.append([
            InlineKeyboardButton(text=f"❌ Αφαίρεση {item['description']}", callback_data=f"removecart_{item['cart_id']}")
        ])
    
    if promo_discount > 0 or promo_tickets > 0:
        inline_keyboard.append([InlineKeyboardButton(text=f"❌ Αφαίρεση Κωδικού ({promo_code})", callback_data="remove_promo")])
    else:
        inline_keyboard.append([InlineKeyboardButton(text="🎟️ Προσθήκη Κωδικού Έκπτωσης", callback_data="ask_promo")])
        
    inline_keyboard.append([InlineKeyboardButton(text=f"💳 Αγορά Όλων ({total_price:.2f}€)", callback_data="checkout")])
    inline_keyboard.append([InlineKeyboardButton(text="🗑 Άδειασμα Καλαθιού", callback_data="clearcart")])

    return text, InlineKeyboardMarkup(inline_keyboard=inline_keyboard)

# --- KEYBOARDS ---
def main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🛍️ Κατάλογος"), KeyboardButton(text="🛒 Καλάθι")],
            [KeyboardButton(text="👛 Πορτοφόλι"), KeyboardButton(text="👤 Το Προφίλ μου")],
            [KeyboardButton(text="🎁 Κλήρωση"), KeyboardButton(text="🎟️ Εκπτωτικοί Κωδικοί")],
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

# --- ADMIN HANDLERS ---
@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    
    await message.answer(
        "👑 **Κρυφό Μενού Διαχειριστή**\n\n"
        "📜 `/add_media` - Ανέβασμα προσφοράς (πολλαπλές φωτό/βίντεο)\n"
        "🗑️ `/remove_media` - Διαγραφή προσφοράς από τον κατάλογο\n"
        "🎟️ `/add_promo <κωδικός> <έκπτωση%> <ώρες> <έξτρα εισιτήρια>` - Π.χ. /add_promo VIP 20 48 2\n"
        "❌ `/del_promo <κωδικός>` - Διαγραφή Promo Code\n"
        "💸 `/give_money <ID> <Ποσό>` - Πιστώσεις υπολοίπου σε χρήστη\n"
        "⏳ `/set_giveaway <ώρες>` - Ορισμός διάρκειας κλήρωσης",
        parse_mode="Markdown"
    )

@dp.message(Command("add_promo"))
async def admin_add_promo(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
        
    args = message.text.split()
    if len(args) < 4 or len(args) > 5:
        await message.answer(
            "⚠️ Χρήση: `/add_promo <ΚΩΔΙΚΟΣ> <Έκπτωση%> <Ώρες> [Extra_Εισιτήρια]`\n"
            "Παράδειγμα (20% έκπτωση για 48 ώρες και 2 εισιτήρια): `/add_promo VIP 20 48 2`", 
            parse_mode="Markdown"
        )
        return
        
    code = args[1].upper()
    try:
        discount = int(args[2])
        if not (0 <= discount <= 100):
            raise ValueError
        hours = float(args[3])
        tickets = int(args[4]) if len(args) == 5 else 0
    except ValueError:
        await message.answer("❌ Η έκπτωση και τα εισιτήρια πρέπει να είναι ακέραιοι, και οι ώρες αριθμός.")
        return
        
    async with db_pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO promo_codes (code, discount, bonus_tickets, expires_at) 
               VALUES ($1, $2, $3, NOW() + ($4 * INTERVAL '1 hour')) 
               ON CONFLICT (code) DO UPDATE 
               SET discount = $2, bonus_tickets = $3, expires_at = NOW() + ($4 * INTERVAL '1 hour');""", 
            code, discount, tickets, hours
        )
        
    await message.answer(f"✅ Ο κωδικός **{code}** ({discount}% έκπτωση, +{tickets} εισιτήρια) αποθηκεύτηκε και λήγει σε {hours} ώρες!", parse_mode="Markdown")

@dp.message(Command("del_promo"))
async def admin_del_promo(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
        
    args = message.text.split()
    if len(args) != 2:
        await message.answer("⚠️ Χρήση: `/del_promo <ΚΩΔΙΚΟΣ>`", parse_mode="Markdown")
        return
        
    code = args[1].upper()
    async with db_pool.acquire() as conn:
        deleted = await conn.execute("DELETE FROM promo_codes WHERE code = $1;", code)
        
    if deleted == "DELETE 0":
        await message.answer(f"⚠️ Ο κωδικός **{code}** δεν βρέθηκε.", parse_mode="Markdown")
    else:
        await message.answer(f"🗑️ Ο κωδικός **{code}** διαγράφηκε επιτυχώς.", parse_mode="Markdown")

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
        await message.answer("❌ Δώσε έναν έγκυρο αριθμό ωρών (π.χ. 5 ή 2.5).")
        return

    async with db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE giveaway_settings SET ends_at = NOW() + ($1 * INTERVAL '1 hour') WHERE id = 1;",
            hours
        )
        await conn.execute("UPDATE users SET giveaway_tickets = 0;")

    await message.answer(f"✅ Η κλήρωση ρυθμίστηκε να λήγει σε {hours} ώρες. Τα παλιά εισιτήρια μηδενίστηκαν!")

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
        await message.answer("❌ Το ID πρέπει να είναι ακέραιος αριθμός και το ποσό αριθμός (π.χ. 10.50).")
        return

    async with db_pool.acquire() as conn:
        user_exists = await conn.fetchval("SELECT 1 FROM users WHERE telegram_id = $1;", target_id)
        if not user_exists:
            await message.answer("❌ Ο χρήστης δεν βρέθηκε στη βάση δεδομένων.")
            return

        await conn.execute("UPDATE users SET balance = balance + $1 WHERE telegram_id = $2;", amount, target_id)

    await message.answer(f"✅ Προστέθηκαν επιτυχώς {amount}€ στο πορτοφόλι του χρήστη {target_id}!")
    try:
        await bot.send_message(target_id, f"🎉 Το υπόλοιπό σου πιστώθηκε με {amount}€ από τον διαχειριστή!", parse_mode="Markdown")
    except Exception:
        pass

# --- ADMIN UPLOAD MEDIA (ΠΟΛΛΑΠΛΑ ΑΡΧΕΙΑ) ---
@dp.message(Command("add_media"))
async def start_upload(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
        
    await state.update_data(file_ids=[], media_types=[])
    await message.answer(
        "📤 **Δημιουργία Προσφοράς**\n\n"
        "Στείλε μου **μία-μία** τις φωτογραφίες ή τα βίντεο.\nΜόλις τελειώσεις, στείλε την εντολή: `/done`",
        parse_mode="Markdown"
    )
    await state.set_state(UploadMedia.waiting_for_files)

@dp.message(UploadMedia.waiting_for_files, F.photo | F.video)
async def receive_media_files(message: types.Message, state: FSMContext):
    data = await state.get_data()
    file_ids = data.get("file_ids", [])
    media_types = data.get("media_types", [])

    if message.photo:
        file_ids.append(message.photo[-1].file_id)
        media_types.append("photo")
    elif message.video:
        file_ids.append(message.video.file_id)
        media_types.append("video")

    await state.update_data(file_ids=file_ids, media_types=media_types)
    await message.answer(f"✅ Προστέθηκε! (Συνολικά: {len(file_ids)} αρχεία). Στείλε κι άλλα ή γράψε `/done`.")

@dp.message(UploadMedia.waiting_for_files, Command("done"))
async def finish_file_upload(message: types.Message, state: FSMContext):
    data = await state.get_data()
    file_ids = data.get("file_ids", [])

    if not file_ids:
        await message.answer("⚠️ Δεν έχεις στείλει κανένα αρχείο! Στείλε τουλάχιστον μία φωτογραφία ή βίντεο.")
        return

    await message.answer("✍️ Γράψε την **περιγραφή** που θα συνοδεύει την προσφορά:", parse_mode="Markdown")
    await state.set_state(UploadMedia.waiting_for_description)

@dp.message(UploadMedia.waiting_for_description, F.text)
async def receive_description(message: types.Message, state: FSMContext):
    await state.update_data(description=message.text)
    await message.answer("💰 Όρισε την **τιμή** σε ευρώ (π.χ. 5.50):", parse_mode="Markdown")
    await state.set_state(UploadMedia.waiting_for_price)

@dp.message(UploadMedia.waiting_for_price, F.text)
async def receive_price(message: types.Message, state: FSMContext):
    try:
        price = float(message.text.replace(",", "."))
        if price <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Παρακαλώ γράψε έναν έγκυρο αριθμό (π.χ. 10 ή 5.50).")
        return

    data = await state.get_data()
    file_ids = data['file_ids']
    media_types = data['media_types']
    description = data['description']

    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO locked_media (file_ids, media_types, description, price) VALUES ($1, $2, $3, $4)",
            file_ids, media_types, description, price
        )
        
    await message.answer(f"✅ Η προσφορά ανέβηκε επιτυχώς στον κατάλογο!")
    await state.clear()

# --- ADMIN DELETE MEDIA ---
@dp.message(Command("remove_media"))
async def admin_remove_media(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    
    async with db_pool.acquire() as conn:
        items = await conn.fetch("SELECT id, description, price FROM locked_media;")
    
    if not items:
        await message.answer("Ο κατάλογος είναι άδειος! Δεν υπάρχουν προσφορές για διαγραφή.")
        return
    
    inline_keyboard = []
    for item in items:
        desc = item['description'][:30] + "..." if len(item['description']) > 30 else item['description']
        inline_keyboard.append([InlineKeyboardButton(text=f"🗑️ Διαγραφή: {desc} ({item['price']}€)", callback_data=f"admindel_{item['id']}")])
    
    await message.answer(
        "🗑️ **Διαγραφή Προσφορών**\nΕπίλεξε ποια προσφορά θέλεις να αφαιρέσεις οριστικά:", 
        reply_markup=InlineKeyboardMarkup(inline_keyboard=inline_keyboard),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data.startswith("admindel_"))
async def process_admin_delete(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    
    media_id = int(callback.data.split("_")[1])
    
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM cart WHERE media_id = $1;", media_id)
        await conn.execute("DELETE FROM locked_media WHERE id = $1;", media_id)
        
    await callback.answer("✅ Η προσφορά διαγράφηκε οριστικά!", show_alert=True)
    await callback.message.delete()


# --- WALLET & TOP-UP HANDLERS ---
@dp.message(F.text == "👛 Πορτοφόλι")
async def show_wallet(message: types.Message):
    async with db_pool.acquire() as conn:
        balance = await conn.fetchval("SELECT balance FROM users WHERE telegram_id = $1;", message.from_user.id)
    
    text = f"👛 **Το Πορτοφόλι μου**\n\nΔιαθέσιμο Υπόλοιπο: **{balance}€**\n\nΕπίλεξε τρόπο κατάθεσης:"
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚡ Αυτόματη Κατάθεση με Crypto", callback_data="crypto_start")],
        [InlineKeyboardButton(text="💳 Κατάθεση με PaySafe (Χειροκίνητη)", callback_data="paysafe_start")]
    ])
    await message.answer(text, reply_markup=keyboard, parse_mode="Markdown")


# --- PAYSAFE FLOW ---
@dp.callback_query(F.data == "paysafe_start")
async def paysafe_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("💶 **Πληκτρολόγησε το ποσό** που θέλεις να καταθέσεις (π.χ. 10 ή 20):", parse_mode="Markdown")
    await state.set_state(PaySafeTopUp.waiting_for_amount)
    await callback.answer()

@dp.message(PaySafeTopUp.waiting_for_amount, F.text)
async def process_paysafe_amount(message: types.Message, state: FSMContext):
    try:
        amount = float(message.text.replace(",", "."))
        if amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Μη έγκυρο ποσό. Σε παρακαλώ γράψε έναν αριθμό (π.χ. 10):")
        return
    
    await state.update_data(amount=amount)
    await message.answer("🔢 **Τώρα γράψε τον 12-ψήφιο κωδικό της PaySafe σου:**", parse_mode="Markdown")
    await state.set_state(PaySafeTopUp.waiting_for_code)

@dp.message(PaySafeTopUp.waiting_for_code, F.text)
async def process_paysafe_code(message: types.Message, state: FSMContext):
    code = message.text.strip().replace(" ", "")
    
    if len(code) != 12 or not code.isdigit():
        await message.answer("❌ ΛΑΘΟΣ! Ο κωδικός πρέπει να αποτελείται από **ακριβώς 12 νούμερα**.", parse_mode="Markdown")
        return
    
    data = await state.get_data()
    amount = data['amount']
    
    admin_text = (
        f"🔔 **ΝΕΟ ΑΙΤΗΜΑ PAYSAFE** 🔔\n\n"
        f"👤 Χρήστης ID: `{message.from_user.id}`\n"
        f"💰 Ποσό: **{amount}€**\n"
        f"🔢 Κωδικός: `{code}`"
    )
    
    admin_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Accept", callback_data=f"ps_acc_{message.from_user.id}_{amount}"),
            InlineKeyboardButton(text="❌ Reject", callback_data=f"ps_rej_{message.from_user.id}")
        ]
    ])
    
    try:
        await bot.send_message(ADMIN_ID, admin_text, reply_markup=admin_kb, parse_mode="Markdown")
        await message.answer("✅ Το αίτημά σου στάλθηκε επιτυχώς! Μόλις ο διαχειριστής επιβεβαιώσει τον κωδικό, τα χρήματα θα μπουν στο πορτοφόλι σου.")
    except Exception as e:
        await message.answer("❌ Υπήρξε ένα σφάλμα κατά την αποστολή.")
        logging.error(f"PaySafe sending to admin failed: {e}")
    
    await state.clear()

@dp.callback_query(F.data.startswith("ps_acc_"))
async def admin_accept_paysafe(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    
    parts = callback.data.split("_")
    user_id = int(parts[2])
    amount = float(parts[3])
    
    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE users SET balance = balance + $1 WHERE telegram_id = $2;", amount, user_id)
        
    await callback.message.edit_text(callback.message.text + "\n\n✅ **ΑΠΟΔΕΚΤΟ - Τα χρήματα πιστώθηκαν!**")
    try:
        await bot.send_message(user_id, f"🎉 **Συγχαρητήρια!** Η κατάθεση PaySafe εγκρίθηκε. **Προστέθηκαν {amount}€**!", parse_mode="Markdown")
    except Exception:
        pass
    await callback.answer("Εγκρίθηκε!")

@dp.callback_query(F.data.startswith("ps_rej_"))
async def admin_reject_paysafe(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    
    parts = callback.data.split("_")
    user_id = int(parts[2])
    
    await callback.message.edit_text(callback.message.text + "\n\n❌ **ΑΠΟΡΡΙΦΘΗΚΕ!**")
    try:
        await bot.send_message(user_id, "❌ Το αίτημα κατάθεσης PaySafe **απορρίφθηκε**.", parse_mode="Markdown")
    except Exception:
        pass
    await callback.answer("Απορρίφθηκε!")


# --- AUTOMATED CRYPTO FLOW (NOWPAYMENTS) ---
@dp.callback_query(F.data == "crypto_start")
async def crypto_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("💶 **Πληκτρολόγησε το ποσό σε Ευρώ** που θέλεις να καταθέσεις (π.χ. 10):", parse_mode="Markdown")
    await state.set_state(CryptoTopUp.waiting_for_amount)
    await callback.answer()

@dp.message(CryptoTopUp.waiting_for_amount, F.text)
async def process_crypto_amount(message: types.Message, state: FSMContext):
    try:
        amount = float(message.text.replace(",", "."))
        if amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Μη έγκυρο ποσό.")
        return
    
    user_id = message.from_user.id
    
    if not NOWPAYMENTS_API_KEY:
        await state.clear()
        await message.answer("❌ Το NOWPayments API Key δεν έχει ρυθμιστεί στο Railway.", parse_mode="Markdown")
        return

    url = "https://api.nowpayments.io/v1/invoice"
    headers = {
        "x-api-key": NOWPAYMENTS_API_KEY,
        "Content-Type": "application/json"
    }
    
    payload = {
        "price_amount": amount,
        "price_currency": "EUR",
        "pay_currency": "ltc",
        "order_id": f"user_{user_id}_crypto_{amount}",
        "order_description": f"Wallet Top-up {amount} EUR"
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=headers) as response:
            if response.status in [200, 201]:
                data = await response.json()
                invoice_url = data.get("invoice_url")
                
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text=f"⚡ Πληρωμή {amount}€ με Crypto", url=invoice_url)]
                ])
                await message.answer(
                    f"🔗 Δημιουργήθηκε ο αυτόματος σύνδεσμος πληρωμής για **{amount}€**.\n\n"
                    f"Πάτα το παρακάτω κουμπί για να πληρώσεις:",
                    reply_markup=keyboard,
                    parse_mode="Markdown"
                )
            else:
                await message.answer("❌ Σφάλμα επικοινωνίας με την υπηρεσία πληρωμών.")
    
    await state.clear()


# --- CATALOG & CART HANDLERS ---
@dp.message(F.text == "🛍️ Κατάλογος")
async def show_catalog(message: types.Message):
    async with db_pool.acquire() as conn:
        items = await conn.fetch("SELECT * FROM locked_media;")
        
    if not items:
        await message.answer("Ο κατάλογος είναι άδειος προς το παρόν!")
        return
        
    for item in items:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"🛒 Προσθήκη στο Καλάθι ({item['price']}€)", callback_data=f"addcart_{item['id']}")]
        ])
        await message.answer(f"🔒 **Κλειδωμένο Αρχείο**\n\n📝 {item['description']}", reply_markup=keyboard)

@dp.callback_query(F.data.startswith("addcart_"))
async def process_add_to_cart(callback: CallbackQuery):
    media_id = int(callback.data.split("_")[1])
    user_id = callback.from_user.id
    
    async with db_pool.acquire() as conn:
        exists = await conn.fetchval("SELECT 1 FROM cart WHERE telegram_id = $1 AND media_id = $2;", user_id, media_id)
        if exists:
            await callback.answer("⚠️ Το έχεις ήδη προσθέσει στο καλάθι σου!", show_alert=True)
            return
        await conn.execute("INSERT INTO cart (telegram_id, media_id) VALUES ($1, $2);", user_id, media_id)
        
    await callback.answer("✅ Προστέθηκε στο καλάθι!", show_alert=False)


@dp.message(F.text == "🛒 Καλάθι")
async def show_cart(message: types.Message, state: FSMContext):
    text, kb = await get_cart_text_and_keyboard(message.from_user.id, state)
    if kb:
        await message.answer(text, reply_markup=kb, parse_mode="Markdown")
    else:
        await message.answer(text)

@dp.callback_query(F.data.startswith("removecart_"))
async def process_remove_from_cart(callback: CallbackQuery, state: FSMContext):
    cart_id = int(callback.data.split("_")[1])
    user_id = callback.from_user.id
    
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM cart WHERE id = $1 AND telegram_id = $2;", cart_id, user_id)
        
    await callback.answer("❌ Το προϊόν αφαιρέθηκε από το καλάθι!", show_alert=False)
    
    text, kb = await get_cart_text_and_keyboard(user_id, state)
    if kb:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")
    else:
        await callback.message.edit_text(text, parse_mode="Markdown")

@dp.callback_query(F.data == "clearcart")
async def process_clear_cart(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM cart WHERE telegram_id = $1;", user_id)
        
    await state.update_data(promo_code=None, promo_discount=0, promo_tickets=0)
        
    await callback.answer("🗑 Το καλάθι σου άδειασε!", show_alert=True)
    await callback.message.edit_text("🛒 Το καλάθι σου είναι πλέον άδειο.")


# --- PROMO CODE HANDLERS ---
@dp.message(F.text == "🎟️ Εκπτωτικοί Κωδικοί")
async def show_promo_codes(message: types.Message):
    async with db_pool.acquire() as conn:
        # 1. Διαγράφουμε όσους έχουν λήξει (αυτόματα)
        await conn.execute("DELETE FROM promo_codes WHERE expires_at <= NOW();")
        # 2. Φέρνουμε τους υπόλοιπους (ενεργούς)
        promos = await conn.fetch("SELECT code, discount, bonus_tickets, expires_at FROM promo_codes;")
        
    if not promos:
        await message.answer("Δεν υπάρχουν διαθέσιμοι εκπτωτικοί κωδικοί αυτή τη στιγμή. Μείνετε συντονισμένοι!")
        return
        
    now = datetime.datetime.now()
    text = "🎟️ **Διαθέσιμοι Εκπτωτικοί Κωδικοί**\n\n"
    
    for p in promos:
        # Υπολογισμός εναπομείναντα χρόνου
        remaining = p['expires_at'] - now
        hours, remainder = divmod(int(remaining.total_seconds()), 3600)
        minutes, _ = divmod(remainder, 60)
        
        text += f"🔹 Κωδικός: `{p['code']}`\n"
        text += f"   ➖ Έκπτωση: **{p['discount']}%**\n"
        if p['bonus_tickets'] > 0:
            text += f"   🎁 Δώρο: **+{p['bonus_tickets']} Εισιτήρια** Κλήρωσης\n"
        text += f"   ⏳ Λήγει σε: **{hours} ώρες και {minutes} λεπτά**\n\n"
        
    text += "💡 *Μπορείς να χρησιμοποιήσεις αυτούς τους κωδικούς μέσα στο καλάθι σου πριν την πληρωμή!*"
    await message.answer(text, parse_mode="Markdown")

@dp.callback_query(F.data == "ask_promo")
async def ask_promo_code(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("🎟️ Στείλε μου στο chat τον κωδικό έκπτωσης που διαθέτεις:")
    await state.set_state(CartPromo.waiting_for_promo)
    await callback.answer()

@dp.message(CartPromo.waiting_for_promo, F.text)
async def apply_promo_code(message: types.Message, state: FSMContext):
    code = message.text.strip().upper()
    
    async with db_pool.acquire() as conn:
        # Καθαρίζουμε τους ληγμένους πρώτα για σιγουριά
        await conn.execute("DELETE FROM promo_codes WHERE expires_at <= NOW();")
        promo = await conn.fetchrow("SELECT discount, bonus_tickets FROM promo_codes WHERE code = $1;", code)
        
    if promo:
        discount = promo['discount']
        tickets = promo['bonus_tickets']
        
        await state.update_data(promo_code=code, promo_discount=discount, promo_tickets=tickets)
        
        msg = f"✅ Ο κωδικός **{code}** εφαρμόστηκε επιτυχώς! Κέρδισες **-{discount}%** έκπτωση."
        if tickets > 0:
            msg += f" Και παίρνεις **+{tickets} εισιτήρια** δώρο για την κλήρωση!"
            
        await message.answer(msg, parse_mode="Markdown")
    else:
        await message.answer("❌ Ο κωδικός που έβαλες είναι άκυρος ή έχει λήξει.")
        
    await state.set_state(None)
    
    text, kb = await get_cart_text_and_keyboard(message.from_user.id, state)
    if kb:
        await message.answer(text, reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "remove_promo")
async def remove_promo_code(callback: CallbackQuery, state: FSMContext):
    await state.update_data(promo_code=None, promo_discount=0, promo_tickets=0)
    await callback.answer("🗑️ Ο εκπτωτικός κωδικός αφαιρέθηκε.", show_alert=False)
    
    text, kb = await get_cart_text_and_keyboard(callback.from_user.id, state)
    if kb:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")


# --- CHECKOUT HANDLER ---
@dp.callback_query(F.data == "checkout")
async def process_checkout(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    
    try:
        async with db_pool.acquire() as conn:
            cart_items = await conn.fetch("""
                SELECT m.description, m.price, m.file_ids, m.media_types
                FROM cart c
                JOIN locked_media m ON c.media_id = m.id
                WHERE c.telegram_id = $1;
            """, user_id)
            
            if not cart_items:
                await callback.answer("⚠️ Το καλάθι σου είναι άδειο.", show_alert=True)
                return
                
            original_price = sum(item['price'] for item in cart_items)
            
            data = await state.get_data()
            promo_discount = data.get("promo_discount", 0)
            promo_tickets = data.get("promo_tickets", 0)
            
            total_price = Decimal(str(original_price))
            if promo_discount > 0:
                multiplier = Decimal(str(1 - promo_discount / 100.0))
                total_price = (total_price * multiplier).quantize(Decimal('0.01'))
                
            user = await conn.fetchrow("SELECT balance, lifetime_points FROM users WHERE telegram_id = $1;", user_id)
            
            if not user:
                await callback.answer("Σφάλμα συστήματος.", show_alert=True)
                return
                
            if user['balance'] < total_price:
                await callback.answer(f"❌ Δεν έχεις αρκετό υπόλοιπο! (Χρειάζεσαι {total_price}€)\nΠήγαινε στο Πορτοφόλι.", show_alert=True)
                return
                
            new_balance = user['balance'] - total_price
            points_earned = int(total_price) 
            old_points = user['lifetime_points']
            new_total_points = old_points + points_earned
            
            tickets_to_add = (new_total_points // 50) - (old_points // 50)
            tickets_to_add += promo_tickets
            
            items_summary = ", ".join([item['description'] for item in cart_items])

            async with conn.transaction():
                await conn.execute(
                    """UPDATE users 
                       SET balance = $1, 
                           lifetime_points = lifetime_points + $2, 
                           giveaway_tickets = giveaway_tickets + $3,
                           total_spent = total_spent + $4 
                       WHERE telegram_id = $5;""",
                    new_balance, points_earned, tickets_to_add, total_price, user_id
                )
                await conn.execute(
                    "INSERT INTO purchases (telegram_id, items_summary, total_price) VALUES ($1, $2, $3);",
                    user_id, f"Αγορά καλαθιού: {items_summary}", total_price
                )
                await conn.execute("DELETE FROM cart WHERE telegram_id = $1;", user_id)
                
        await state.update_data(promo_code=None, promo_discount=0, promo_tickets=0)
                
        await callback.answer("✅ Η αγορά ήταν επιτυχής!", show_alert=False)
        await callback.message.edit_text("✅ Η αγορά ολοκληρώθηκε με επιτυχία! Σας αποστέλλονται τα αρχεία...")

        if tickets_to_add > 0:
            try:
                await bot.send_message(
                    user_id, 
                    f"🎉 **Συγχαρητήρια! Πήρες συνολικά {tickets_to_add} εισιτήριο(α) για την κλήρωση!**", 
                    parse_mode="Markdown"
                )
            except Exception:
                pass
        
        for item in cart_items:
            try:
                file_ids = item['file_ids']
                media_types = item['media_types']
                description = item['description']

                if len(file_ids) == 1:
                    if media_types[0] == "photo":
                        await bot.send_photo(chat_id=user_id, photo=file_ids[0], caption=f"🎉 {description}")
                    elif media_types[0] == "video":
                        await bot.send_video(chat_id=user_id, video=file_ids[0], caption=f"🎉 {description}")
                else:
                    media_group = []
                    for i, (f_id, m_type) in enumerate(zip(file_ids, media_types)):
                        caption = f"🎉 {description}" if i == 0 else None
                        if m_type == "photo":
                            media_group.append(InputMediaPhoto(media=f_id, caption=caption))
                        else:
                            media_group.append(InputMediaVideo(media=f_id, caption=caption))
                            
                    await bot.send_media_group(chat_id=user_id, media=media_group)
                    
            except Exception as e:
                logging.error(f"Error sending media to {user_id}: {e}")
                
    except Exception as e:
        logging.error(f"Critical error during checkout for user {user_id}: {e}")
        await callback.answer("❌ Προέκυψε σφάλμα κατά την ολοκλήρωση της αγοράς. Δοκιμάστε ξανά.", show_alert=True)


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
        f"Καλωσόρισες, {message.from_user.first_name}!\nΧρησιμοποίησε το μενού παρακάτω για να πλοηγηθείς.",
        reply_markup=main_menu()
    )

@dp.message(F.text == "👥 Κοινότητα & Επικοινωνία")
async def show_support(message: types.Message):
    await message.answer(
        "Μπορείς να συνδεθείς στην κοινότητά μας ή να επικοινωνήσεις απευθείας μαζί μας παρακάτω:",
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
    tickets = user['giveaway_tickets']
    level_title = get_user_level(points)

    profile_text = (
        f"👤 **Το Προφίλ σου**\n\n"
        f"👛 **Υπόλοιπο:** {balance}€\n"
        f"⭐ **Πόντοι:** {points}\n"
        f"🎖️ **Τίτλος / Επίπεδο:** {level_title}\n"
        f"🎟️ **Εισιτήρια Κλήρωσης:** {tickets}\n\n"
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
        settings = await conn.fetchrow("SELECT ends_at FROM giveaway_settings WHERE id = 1;")
        now = datetime.datetime.now()
        
        if settings and settings['ends_at'] <= now:
            text = (
                f"🎁 **Μεγάλη Κλήρωση**\n\n"
                f"🏁 **Η προηγούμενη κλήρωση έχει λήξει!**\n"
                f"⏳ Σύντομα θα ξεκινήσει καινούρια. Μείνε συντονισμένος!\n\n"
                f"💡 *Κάθε 50 πόντοι (10€ αγορών) σου εξασφαλίζουν αυτόματα 1 εισιτήριο!*"
            )
        else:
            total_tickets = await conn.fetchval("SELECT SUM(giveaway_tickets) FROM users;") or 0
            
            remaining_time = settings['ends_at'] - now
            hours, remainder = divmod(int(remaining_time.total_seconds()), 3600)
            minutes, _ = divmod(remainder, 60)

            text = (
                f"🎁 **Μεγάλη Κλήρωση**\n\n"
                f"🎟️ **Συνολικά Εισιτήρια που έχουν δοθεί:** {total_tickets}\n"
                f"⏳ **Λήξη Κλήρωσης σε:** {hours} ώρες και {minutes} λεπτά!\n\n"
                f"💡 *Κάθε 50 πόντοι (10€ αγορών) σου εξασφαλίζουν αυτόματα 1 εισιτήριο!*"
            )
            
    await message.answer(text, parse_mode="Markdown")

@dp.message(F.text == "ℹ️ Info")
async def show_info(message: types.Message):
    await message.answer("Εδώ προσθέτεις πληροφορίες για το κανάλι ή τους όρους χρήσης.")

# --- MAIN EXECUTION ---
async def main():
    logging.basicConfig(level=logging.INFO)
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())