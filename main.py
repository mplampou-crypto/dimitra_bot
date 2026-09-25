import asyncio
import datetime
import logging
import os
import aiohttp
import random
import string
import urllib.parse
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

# Διευθύνσεις Πορτοφολιών Crypto
BTC_WALLET = os.getenv("BTC_WALLET", "1waQSukZCthRAwc7D97hh918gG7CwXz2N")
ETH_WALLET = os.getenv("ETH_WALLET", "0x0000000000000000000000000000000000000000")
LTC_WALLET = os.getenv("LTC_WALLET", "Ltc1q0000000000000000000000000000000000000")

# Εδώ βάζεις τα IDs σας αν δεν τα τραβάει από το περιβάλλον
ADMIN_IDS = [int(admin_id.strip()) for admin_id in os.getenv("ADMIN_IDS", "123456789,987654321").split(",") if admin_id.strip()]

GROUP_LINK = "https://t.me/+h9QI608rXMUxOWI0"
PREMIUM_GROUP_LINK = "https://t.me/+tJ-TlvZpt2Y5YTA8" # ΑΛΛΑΞΕ ΤΟ ΜΕ ΤΟ LINK ΤΗΣ ΣΥΝΔΡΟΜΗΤΙΚΗΣ ΟΜΑΔΑΣ
ADMIN_LINK = "https://t.me/dimitrasavvidi"

# Το γραφικό που θα στέλνεται όταν κάποιος έχει ήδη εκκρεμές αίτημα
PENDING_GRAPHIC_URL = "https://dummyimage.com/600x400/1a1a1a/ffcc00&text=Please+Wait+For+Approval"

bot = Bot(token=TOKEN)
dp = Dispatcher()
db_pool = None

# --- FSM STATES ---
class UploadMedia(StatesGroup):
    waiting_for_files = State()
    waiting_for_description = State()
    waiting_for_price = State()

class UploadCustom(StatesGroup):
    waiting_for_files = State()
    waiting_for_description = State()
    waiting_for_price = State()

class PaySafeTopUp(StatesGroup):
    waiting_for_amount = State()
    waiting_for_code = State()

class CryptoTopUp(StatesGroup):
    waiting_for_currency = State()
    waiting_for_amount = State()
    waiting_for_screenshot = State()

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
                file_ids TEXT[] DEFAULT ARRAY[]::TEXT[],
                media_types TEXT[] DEFAULT ARRAY[]::TEXT[],
                description TEXT,
                price NUMERIC(10, 2) NOT NULL,
                is_subscription BOOLEAN DEFAULT FALSE,
                duration_months INT DEFAULT 0,
                is_custom BOOLEAN DEFAULT FALSE
            );
        """)
        
        await connection.execute("""
            CREATE TABLE IF NOT EXISTS active_subscriptions (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT UNIQUE,
                user_full_name TEXT,
                expires_at TIMESTAMP NOT NULL,
                notified_expiry BOOLEAN DEFAULT FALSE
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
        
        await connection.execute("""
            CREATE TABLE IF NOT EXISTS paysafe_requests (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT,
                amount NUMERIC(10, 2),
                code TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT NOW()
            );
        """)

        await connection.execute("""
            CREATE TABLE IF NOT EXISTS crypto_requests (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT,
                amount NUMERIC(10, 2),
                currency TEXT,
                photo_file_id TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT NOW()
            );
        """)

        await connection.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS lifetime_points INT DEFAULT 0;")
        await connection.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS giveaway_tickets INT DEFAULT 0;")
        await connection.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS total_spent NUMERIC(10, 2) DEFAULT 0.00;")
        
        await connection.execute("ALTER TABLE promo_codes ADD COLUMN IF NOT EXISTS bonus_tickets INT DEFAULT 0;")
        await connection.execute("ALTER TABLE promo_codes ADD COLUMN IF NOT EXISTS expires_at TIMESTAMP;")
        await connection.execute("UPDATE promo_codes SET expires_at = NOW() + INTERVAL '30 days' WHERE expires_at IS NULL;")
        
        await connection.execute("ALTER TABLE locked_media ADD COLUMN IF NOT EXISTS file_ids TEXT[];")
        await connection.execute("ALTER TABLE locked_media ADD COLUMN IF NOT EXISTS media_types TEXT[];")
        await connection.execute("ALTER TABLE locked_media ADD COLUMN IF NOT EXISTS is_subscription BOOLEAN DEFAULT FALSE;")
        await connection.execute("ALTER TABLE locked_media ADD COLUMN IF NOT EXISTS duration_months INT DEFAULT 0;")
        await connection.execute("ALTER TABLE locked_media ADD COLUMN IF NOT EXISTS is_custom BOOLEAN DEFAULT FALSE;")

# --- HELPER FUNCTIONS ---
def get_user_level(points: int) -> str:
    if points >= 1000:
        return "Ultimate VIP❤️🔞"
    elif points >= 500:
        return "Αφέντης💋👑🔞"
    elif points >= 300:
        return "Ορεξάτος👀🔥🔞"
    elif points >= 150:
        return "Τολμηρός💋🔞"
    else:
        return "Πρωτάρης🐣🔞"

def get_level_progress(points: int):
    if points < 150:
        min_p, max_p, next_level = 0, 150, "Πρωτάρης🐣🔞"
    elif points < 300:
        min_p, max_p, next_level = 150, 300, "Τολμηρός💋🔞"
    elif points < 500:
        min_p, max_p, next_level = 300, 500, "Ορεξάτος👀🔥🔞"
    elif points < 1000:
        min_p, max_p, next_level = 500, 1000, "Αφέντης💋👑🔞"
    elif points >= 1000:
        min_p, max_p, next_level = 1000, 2000, "Ultimate VIP❤️🔞"
    else:
        return "🟥🟥🟥🟥🟥🟥🟥🟥🟥🟥 100%", "🎉 Έφτασες στο μέγιστο Level (VIP)!"

    progress_percent = (points - min_p) / (max_p - min_p) * 100
    filled_blocks = int(progress_percent // 10)
    empty_blocks = 10 - filled_blocks
    
    bar = "🟥" * filled_blocks + "⬜" * empty_blocks
    points_needed = max_p - points
    return f"{bar} {int(progress_percent)}%", f"{points_needed} πόντοι για ξεκλείδωμα του {next_level}!"

def generate_order_code():
    return "KB-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=6))

async def get_conversion(amount_eur: float, crypto_symbol: str):
    try:
        async with aiohttp.ClientSession() as session:
            usdt_amount = 0.0
            crypto_amount = 0.0
            
            async with session.get("https://api.bybit.com/v5/market/tickers?category=spot&symbol=EURUSDT") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    result_list = data.get("result", {}).get("list", [])
                    if result_list:
                        eur_to_usdt = float(result_list[0]['lastPrice'])
                        usdt_amount = amount_eur * eur_to_usdt

            async with session.get(f"https://api.bybit.com/v5/market/tickers?category=spot&symbol={crypto_symbol}EUR") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    result_list = data.get("result", {}).get("list", [])
                    if result_list:
                        crypto_price_eur = float(result_list[0]['lastPrice'])
                        crypto_amount = amount_eur / crypto_price_eur
                    
            return usdt_amount, crypto_amount
    except Exception as e:
        logging.error(f"Error fetching crypto prices from Bybit: {e}")
        return 0.0, 0.0

async def has_pending_request(user_id: int) -> bool:
    async with db_pool.acquire() as conn:
        pending_ps = await conn.fetchval("SELECT 1 FROM paysafe_requests WHERE telegram_id = $1 AND status = 'pending';", user_id)
        pending_cr = await conn.fetchval("SELECT 1 FROM crypto_requests WHERE telegram_id = $1 AND status = 'pending';", user_id)
        return bool(pending_ps or pending_cr)

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

# --- BACKGROUND TASK ΓΙΑ ΕΛΕΓΧΟ ΛΗΞΗΣ ΣΥΝΔΡΟΜΩΝ ---
async def check_subscriptions_loop():
    while True:
        try:
            now = datetime.datetime.now()
            async with db_pool.acquire() as conn:
                expired_subs = await conn.fetch("""
                    SELECT telegram_id, user_full_name, expires_at 
                    FROM active_subscriptions 
                    WHERE expires_at <= $1 AND notified_expiry = FALSE;
                """, now)
                
                for sub in expired_subs:
                    t_id = sub['telegram_id']
                    name = sub['user_full_name'] or "Άγνωστος"
                    
                    for admin_id in ADMIN_IDS:
                        try:
                            await bot.send_message(
                                admin_id,
                                f"⚠️ **ΕΛΗΞΕ Η ΣΥΝΔΡΟΜΗ ΧΡΗΣΤΗ!** ⚠️\n\n"
                                f"👤 Όνομα: **{name}**\n"
                                f"🆔 ID: `{t_id}`\n"
                                f"📅 Έληξε στις: {sub['expires_at'].strftime('%d/%m/%Y %H:%M')}\n\n"
                                f"👉 *Παρακαλώ αφαίρεσε τον χρήστη από την premium ομάδα!*",
                                parse_mode="Markdown"
                            )
                        except Exception as e:
                            logging.error(f"Failed to notify admin {admin_id} about expired sub: {e}")
                    
                    await conn.execute(
                        "UPDATE active_subscriptions SET notified_expiry = TRUE WHERE telegram_id = $1;", 
                        t_id
                    )
        except Exception as e:
            logging.error(f"Error in background subscription checker: {e}")
            
        await asyncio.sleep(60)

# --- ADMIN HANDLERS ---
@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    
    await message.answer(
        "👑 **Κρυφό Μενού Διαχειριστή**\n\n"
        "📜 `/add_media` - Ανέβασμα κανονικού αρχείου/φωτό\n"
        "⌨️ `/add_custom` - Ανέβασμα Custom Προϊόντος (με κωδικό παραγγελίας)\n"
        "⭐ `/add_sub   ` - Προσθήκη συνδρομής ομάδας\n"
        "🗑️ `/remove_media` - Διαγραφή προσφοράς από τον κατάλογο\n"
        "🎟️ `/add_promo    `\n"
        "❌ `/del_promo `\n"
        "💸 `/give_money  `\n"
        "⏳ `/set_giveaway `",
        parse_mode="Markdown"
    )

@dp.message(Command("add_sub"))
async def admin_add_subscription(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
        
    args = message.text.split(maxsplit=3)
    if len(args) < 4:
        await message.answer(
            "⚠️ Χρήση: `/add_sub   `\n"
            "Παράδειγμα: `/add_sub 10 1 ❌ ΑΠΟ 20€ -> ✅ ΜΟΝΟ 10€! Πρόσβαση στην VIP ομάδα για 1 Μήνα.`",
            parse_mode="Markdown"
        )
        return
        
    try:
        price = float(args[1].replace(",", "."))
        months = int(args[2])
        description = args[3]
        if price <= 0 or months <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Η τιμή και οι μήνες πρέπει να είναι θετικοί αριθμοί.")
        return
        
    async with db_pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO locked_media (file_ids, media_types, description, price, is_subscription, duration_months, is_custom) 
               VALUES (ARRAY[]::TEXT[], ARRAY[]::TEXT[], $1, $2, TRUE, $3, FALSE);""",
            description, price, months
        )
        
    await message.answer(f"✅ Η συνδρομή ({months} μήνα/ες - {price}€) προστέθηκε επιτυχώς στον κατάλογο!")

@dp.message(Command("add_promo"))
async def admin_add_promo(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
        
    args = message.text.split()
    if len(args) < 4 or len(args) > 5:
        await message.answer(
            "⚠️ Χρήση: `/add_promo    [Extra_Εισιτήρια]`\n"
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
    if message.from_user.id not in ADMIN_IDS:
        return
        
    args = message.text.split()
    if len(args) != 2:
        await message.answer("⚠️ Χρήση: `/del_promo `", parse_mode="Markdown")
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
    if message.from_user.id not in ADMIN_IDS:
        return

    args = message.text.split()
    if len(args) != 2:
        await message.answer("⚠️ Χρήση: `/set_giveaway `", parse_mode="Markdown")
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
    if message.from_user.id not in ADMIN_IDS:
        return

    args = message.text.split()
    if len(args) != 3:
        await message.answer("⚠️ Χρήση: `/give_money  `", parse_mode="Markdown")
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

# --- ADMIN UPLOAD CUSTOM PRODUCT ---
@dp.message(Command("add_custom"))
async def start_custom_upload(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
        
    await state.update_data(file_ids=[], media_types=[])
    await message.answer(
        "⌨️ **Δημιουργία Custom Προϊόντος (π.χ. Keyboard)**\n\n"
        "Στείλε μου **μία-μία** τις φωτογραφίες ή τα βίντεο του προϊόντος.\nΜόλις τελειώσεις, στείλε την εντολή: `/done_custom`",
        parse_mode="Markdown"
    )
    await state.set_state(UploadCustom.waiting_for_files)

@dp.message(UploadCustom.waiting_for_files, F.photo | F.video)
async def receive_custom_files(message: types.Message, state: FSMContext):
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
    await message.answer(f"✅ Προστέθηκε! (Συνολικά: {len(file_ids)} αρχεία). Στείλε κι άλλα ή γράψε `/done_custom`.")

@dp.message(UploadCustom.waiting_for_files, Command("done_custom"))
async def finish_custom_upload(message: types.Message, state: FSMContext):
    data = await state.get_data()
    file_ids = data.get("file_ids", [])

    if not file_ids:
        await message.answer("⚠️ Δεν έχεις στείλει κανένα αρχείο! Στείλε τουλάχιστον μία φωτογραφία ή βίντεο.")
        return

    await message.answer("✍️ Γράψε την **περιγραφή** που θα συνοδεύει το custom προϊόν:", parse_mode="Markdown")
    await state.set_state(UploadCustom.waiting_for_description)

@dp.message(UploadCustom.waiting_for_description, F.text)
async def receive_custom_description(message: types.Message, state: FSMContext):
    await state.update_data(description=message.text)
    await message.answer("💰 Όρισε την **τιμή** σε ευρώ (π.χ. 65.50):", parse_mode="Markdown")
    await state.set_state(UploadCustom.waiting_for_price)

@dp.message(UploadCustom.waiting_for_price, F.text)
async def receive_custom_price(message: types.Message, state: FSMContext):
    try:
        price = float(message.text.replace(",", "."))
        if price <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Παρακαλώ γράψε έναν έγκυρο αριθμό (π.χ. 50).")
        return

    data = await state.get_data()
    file_ids = data['file_ids']
    media_types = data['media_types']
    description = data['description']

    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO locked_media (file_ids, media_types, description, price, is_subscription, duration_months, is_custom) VALUES ($1, $2, $3, $4, FALSE, 0, TRUE)",
            file_ids, media_types, description, price
        )
        
    await message.answer(f"✅ Το Custom Προϊόν ανέβηκε επιτυχώς στον κατάλογο!")
    await state.clear()


# --- ADMIN UPLOAD MEDIA (ΠΟΛΛΑΠΛΑ ΑΡΧΕΙΑ) ---
@dp.message(Command("add_media"))
async def start_upload(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
        
    await state.update_data(file_ids=[], media_types=[])
    await message.answer(
        "📤 **Δημιουργία Προσφοράς Αρχείων**\n\n"
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
            "INSERT INTO locked_media (file_ids, media_types, description, price, is_subscription, duration_months, is_custom) VALUES ($1, $2, $3, $4, FALSE, 0, FALSE)",
            file_ids, media_types, description, price
        )
        
    await message.answer(f"✅ Η προσφορά ανέβηκε επιτυχώς στον κατάλογο!")
    await state.clear()

# --- ADMIN DELETE MEDIA ---
@dp.message(Command("remove_media"))
async def admin_remove_media(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    
    async with db_pool.acquire() as conn:
        items = await conn.fetch("SELECT id, description, price, is_subscription, is_custom FROM locked_media;")
    
    if not items:
        await message.answer("Ο κατάλογος είναι άδειος! Δεν υπάρχουν προσφορές για διαγραφή.")
        return
    
    inline_keyboard = []
    for item in items:
        desc = item['description'][:30] + "..." if len(item['description']) > 30 else item['description']
        if item['is_subscription']:
            prefix = "⭐ [Συνδρομή] "
        elif item.get('is_custom'):
            prefix = "⌨️ [Custom] "
        else:
            prefix = "📦 "
        inline_keyboard.append([InlineKeyboardButton(text=f"🗑️ Διαγραφή: {prefix}{desc} ({item['price']}€)", callback_data=f"admindel_{item['id']}")])
    
    await message.answer(
        "🗑️ **Διαγραφή Προσφορών**\nΕπίλεξε ποια προσφορά θέλεις να αφαιρέσεις οριστικά:", 
        reply_markup=InlineKeyboardMarkup(inline_keyboard=inline_keyboard),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data.startswith("admindel_"))
async def process_admin_delete(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
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
        [InlineKeyboardButton(text="⚡ Κατάθεση με Crypto ", callback_data="crypto_start")],
        [InlineKeyboardButton(text="💳 Κατάθεση με PaySafe ", callback_data="paysafe_start")]
    ])
    await message.answer(text, reply_markup=keyboard, parse_mode="Markdown")


# --- PAYSAFE FLOW ---
@dp.callback_query(F.data == "paysafe_start")
async def paysafe_start(callback: CallbackQuery, state: FSMContext):
    if await has_pending_request(callback.from_user.id):
        await callback.message.answer_photo(
            photo=PENDING_GRAPHIC_URL,
            caption="⏳ **Εκκρεμεί ήδη ένα αίτημα κατάθεσης!**\n\nΠαρακαλώ περίμενε να εγκριθεί ή να απορριφθεί η προηγούμενη κατάθεσή σου από τους διαχειριστές πριν κάνεις καινούρια.",
            parse_mode="Markdown"
        )
        await callback.answer()
        return

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
    user_id = message.from_user.id
    
    async with db_pool.acquire() as conn:
        req_id = await conn.fetchval(
            "INSERT INTO paysafe_requests (telegram_id, amount, code) VALUES ($1, $2, $3) RETURNING id;",
            user_id, amount, code
        )
    
    admin_text = (
        f"🔔 **ΝΕΟ ΑΙΤΗΜΑ PAYSAFE** 🔔\n\n"
        f"🆔 Αίτημα #: `{req_id}`\n"
        f"👤 Χρήστης ID: `{user_id}`\n"
        f"💰 Ποσό: **{amount}€**\n"
        f"🔢 Κωδικός: `{code}`"
    )
    
    admin_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Accept", callback_data=f"ps_acc_{req_id}"),
            InlineKeyboardButton(text="❌ Reject", callback_data=f"ps_rej_{req_id}")
        ]
    ])
    
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, admin_text, reply_markup=admin_kb, parse_mode="Markdown")
        except Exception as e:
            logging.error(f"PaySafe sending to admin {admin_id} failed: {e}")
            
    await message.answer("✅ Το αίτημά σου στάλθηκε επιτυχώς! Μόλις ο διαχειριστής επιβεβαιώσει τον κωδικό, τα χρήματα θα μπουν στο πορτοφόλι σου.")
    await state.clear()

@dp.callback_query(F.data.startswith("ps_acc_"))
async def admin_accept_paysafe(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    
    req_id = int(callback.data.split("_")[2])
    
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT telegram_id, amount, status FROM paysafe_requests WHERE id = $1;", req_id)
        
        if not row:
            await callback.answer("❌ Το αίτημα δεν βρέθηκε στη βάση!", show_alert=True)
            return
            
        if row['status'] != 'pending':
            await callback.answer("⚠️ Αυτό το αίτημα έχει ΉΔΗ ολοκληρωθεί (από εσένα ή τον άλλο admin)!", show_alert=True)
            await callback.message.edit_reply_markup(reply_markup=None)
            return
            
        user_id = row['telegram_id']
        amount = row['amount']
        
        async with conn.transaction():
            await conn.execute("UPDATE paysafe_requests SET status = 'accepted' WHERE id = $1;", req_id)
            await conn.execute("UPDATE users SET balance = balance + $1 WHERE telegram_id = $2;", amount, user_id)
        
    await callback.message.edit_text(callback.message.text + f"\n\n✅ **ΑΠΟΔΕΚΤΟ από {callback.from_user.first_name} - Τα χρήματα πιστώθηκαν!**")
    try:
        await bot.send_message(user_id, f"🎉 **Συγχαρητήρια!** Η κατάθεση PaySafe εγκρίθηκε. **Προστέθηκαν {amount}€**!", parse_mode="Markdown")
    except Exception:
        pass
    await callback.answer("Εγκρίθηκε!")

@dp.callback_query(F.data.startswith("ps_rej_"))
async def admin_reject_paysafe(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    
    req_id = int(callback.data.split("_")[2])
    
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT telegram_id, status FROM paysafe_requests WHERE id = $1;", req_id)
        
        if not row:
            await callback.answer("❌ Το αίτημα δεν βρέθηκε στη βάση!", show_alert=True)
            return
            
        if row['status'] != 'pending':
            await callback.answer("⚠️ Αυτό το αίτημα έχει ΉΔΗ ολοκληρωθεί!", show_alert=True)
            await callback.message.edit_reply_markup(reply_markup=None)
            return
            
        user_id = row['telegram_id']
        await conn.execute("UPDATE paysafe_requests SET status = 'rejected' WHERE id = $1;", req_id)
    
    await callback.message.edit_text(callback.message.text + f"\n\n❌ **ΑΠΟΡΡΙΦΘΗΚΕ από {callback.from_user.first_name}!**")
    try:
        await bot.send_message(user_id, "❌ Το αίτημα κατάθεσης PaySafe **απορρίφθηκε**.", parse_mode="Markdown")
    except Exception:
        pass
    await callback.answer("Απορρίφθηκε!")


# --- MANUAL CRYPTO FLOW ---
@dp.callback_query(F.data == "crypto_start")
async def crypto_start(callback: CallbackQuery, state: FSMContext):
    if await has_pending_request(callback.from_user.id):
        await callback.message.answer_photo(
            photo=PENDING_GRAPHIC_URL,
            caption="⏳ **Εκκρεμεί ήδη ένα αίτημα κατάθεσης!**\n\nΠαρακαλώ περίμενε να εγκριθεί ή να απορριφθεί η προηγούμενη κατάθεσή σου από τους διαχειριστές πριν κάνεις καινούρια.",
            parse_mode="Markdown"
        )
        await callback.answer()
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="₿ Bitcoin (BTC)", callback_data="crypto_curr_BTC"),
            InlineKeyboardButton(text="🔷 Ethereum (ETH)", callback_data="crypto_curr_ETH")
        ],
        [
            InlineKeyboardButton(text="⚡ Litecoin (LTC)", callback_data="crypto_curr_LTC")
        ]
    ])
    await callback.message.answer("🪙 **Επίλεξε το κρυπτονόμισμα** με το οποίο θέλεις να καταθέσεις:", reply_markup=keyboard, parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data.startswith("crypto_curr_"))
async def process_crypto_currency(callback: CallbackQuery, state: FSMContext):
    currency = callback.data.split("_")[2]
    await state.update_data(currency=currency)
    
    await callback.message.answer(f"💶 **Πληκτρολόγησε το ποσό σε Ευρώ** που θέλεις να καταθέσεις σε **{currency}** (π.χ. 10 ή 20):", parse_mode="Markdown")
    await state.set_state(CryptoTopUp.waiting_for_amount)
    await callback.answer()

@dp.message(CryptoTopUp.waiting_for_amount, F.text)
async def process_crypto_amount(message: types.Message, state: FSMContext):
    try:
        amount = float(message.text.replace(",", "."))
        if amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Μη έγκυρο ποσό. Σε παρακαλώ γράψε έναν αριθμό (π.χ. 10):")
        return
    
    data = await state.get_data()
    currency = data.get("currency", "BTC")
    
    await state.update_data(amount=amount)
    
    wait_msg = await message.answer("⏳ Γίνεται υπολογισμός ισοτιμίας σε πραγματικό χρόνο...")
    usdt_amt, crypto_amt = await get_conversion(amount, currency)
    await wait_msg.delete()
    
    wallets = {
        "BTC": BTC_WALLET,
        "ETH": ETH_WALLET,
        "LTC": LTC_WALLET
    }
    networks = {
        "BTC": "Bitcoin Network",
        "ETH": "ERC20",
        "LTC": "Litecoin Network"
    }
    
    wallet_address = wallets.get(currency, "Δεν έχει οριστεί διεύθυνση")
    network_name = networks.get(currency, "Γνωστό δίκτυο")
    
    crypto_text = f"{crypto_amt:.6f}" if crypto_amt > 0 else "Αγνωστο (API Error)"
    usdt_text = f"{usdt_amt:.2f}" if usdt_amt > 0 else "Αγνωστο (API Error)"
    
    msg_text = (
        f"📥 **Στοιχεία Πληρωμής ({currency})**\n\n"
        f"💶 **Ποσό Κατάθεσης:** {amount}€\n"
        f"💵 **Αντιστοιχία USDT:** ~{usdt_text} USDT\n"
        f"🪙 **Σε {currency}:** ~{crypto_text} {currency}\n"
        f"🔗 **Δίκτυο (Network):** {network_name}\n\n"
        f"📍 **Διεύθυνση Πορτοφολιού:**\n`{wallet_address}`\n\n"
        f"📸 **Μόλις κάνεις τη μεταφορά, στείλε μου εδώ σε φωτογραφία το screenshot της συναλλαγής!**\n"
        f"_(Σημείωση: Φρόντισε να στείλεις την ακριβή αξία σε Crypto ή USDT)_"
    )
    await message.answer(msg_text, parse_mode="Markdown")
    await state.set_state(CryptoTopUp.waiting_for_screenshot)

@dp.message(CryptoTopUp.waiting_for_screenshot, F.photo)
async def process_crypto_screenshot(message: types.Message, state: FSMContext):
    photo_file_id = message.photo[-1].file_id
    data = await state.get_data()
    amount = data.get("amount", 0.0)
    currency = data.get("currency", "Crypto")
    user_id = message.from_user.id
    
    async with db_pool.acquire() as conn:
        req_id = await conn.fetchval(
            "INSERT INTO crypto_requests (telegram_id, amount, currency, photo_file_id) VALUES ($1, $2, $3, $4) RETURNING id;",
            user_id, amount, currency, photo_file_id
        )
    
    admin_caption = (
        f"🔔 **ΝΕΟ ΑΙΤΗΜΑ CRYPTO** 🔔\n\n"
        f"🆔 Αίτημα #: `{req_id}`\n"
        f"👤 Χρήστης ID: `{user_id}`\n"
        f"💰 Ποσό: **{amount}€**\n"
        f"🪙 Νόμισμα: **{currency}**"
    )
    
    admin_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Accept", callback_data=f"cr_acc_{req_id}"),
            InlineKeyboardButton(text="❌ Reject", callback_data=f"cr_rej_{req_id}")
        ]
    ])
    
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_photo(chat_id=admin_id, photo=photo_file_id, caption=admin_caption, reply_markup=admin_kb, parse_mode="Markdown")
        except Exception as e:
            logging.error(f"Crypto sending to admin {admin_id} failed: {e}")
            
    await message.answer("✅ Το screenshot στάλθηκε επιτυχώς! Μόλις ο διαχειριστής επιβεβαιώσει τη συναλλαγή, τα χρήματα θα μπουν στο πορτοφόλι σου.")
    await state.clear()

@dp.message(CryptoTopUp.waiting_for_screenshot, ~F.photo)
async def process_crypto_not_photo(message: types.Message):
    await message.answer("❌ Παρακαλώ στείλε **φωτογραφία (screenshot)** της συναλλαγής!")

@dp.callback_query(F.data.startswith("cr_acc_"))
async def admin_accept_crypto(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    
    req_id = int(callback.data.split("_")[2])
    
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT telegram_id, amount, currency, status FROM crypto_requests WHERE id = $1;", req_id)
        
        if not row:
            await callback.answer("❌ Το αίτημα δεν βρέθηκε στη βάση!", show_alert=True)
            return
            
        if row['status'] != 'pending':
            await callback.answer("⚠️ Αυτό το αίτημα έχει ΉΔΗ ολοκληρωθεί (από εσένα ή τον άλλο admin)!", show_alert=True)
            await callback.message.edit_reply_markup(reply_markup=None)
            return
            
        user_id = row['telegram_id']
        amount = row['amount']
        
        async with conn.transaction():
            await conn.execute("UPDATE crypto_requests SET status = 'accepted' WHERE id = $1;", req_id)
            await conn.execute("UPDATE users SET balance = balance + $1 WHERE telegram_id = $2;", amount, user_id)
        
    await callback.message.edit_caption(caption=callback.message.caption + f"\n\n✅ **ΑΠΟΔΕΚΤΟ από {callback.from_user.first_name} - Τα χρήματα πιστώθηκαν!**")
    try:
        await bot.send_message(user_id, f"🎉 **Συγχαρητήρια!** Η κατάθεση {row['currency']} εγκρίθηκε. **Προστέθηκαν {amount}€**!", parse_mode="Markdown")
    except Exception:
        pass
    await callback.answer("Εγκρίθηκε!")

@dp.callback_query(F.data.startswith("cr_rej_"))
async def admin_reject_crypto(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    
    req_id = int(callback.data.split("_")[2])
    
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT telegram_id, currency, status FROM crypto_requests WHERE id = $1;", req_id)
        
        if not row:
            await callback.answer("❌ Το αίτημα δεν βρέθηκε στη βάση!", show_alert=True)
            return
            
        if row['status'] != 'pending':
            await callback.answer("⚠️ Αυτό το αίτημα έχει ΉΔΗ ολοκληρωθεί!", show_alert=True)
            await callback.message.edit_reply_markup(reply_markup=None)
            return
            
        user_id = row['telegram_id']
        await conn.execute("UPDATE crypto_requests SET status = 'rejected' WHERE id = $1;", req_id)
    
    await callback.message.edit_caption(caption=callback.message.caption + f"\n\n❌ **ΑΠΟΡΡΙΦΘΗΚΕ από {callback.from_user.first_name}!**")
    try:
        await bot.send_message(user_id, f"❌ Το αίτημα κατάθεσης {row['currency']} **απορρίφθηκε**.", parse_mode="Markdown")
    except Exception:
        pass
    await callback.answer("Απορρίφθηκε!")


# --- CATALOG & CART HANDLERS ---
@dp.message(F.text == "🛍️ Κατάλογος")
async def show_catalog(message: types.Message):
    async with db_pool.acquire() as conn:
        items = await conn.fetch("SELECT * FROM locked_media;")
        
    if not items:
        await message.answer("Ο κατάλογος είναι άδειος προς το παρόν!")
        return
        
    for item in items:
        if item['is_subscription']:
            months_word = "Μήνας" if item['duration_months'] == 1 else "Μήνες"
            title = f"⭐ **Συνδρομή Ομάδας** ({item['duration_months']} {months_word})\n\n"
        elif item.get('is_custom'):
            title = f"⌨️ **Custom Παραγγελία**\n\n"
        else:
            title = f"🔒 **Κλειδωμένο Αρχείο**\n\n"
            
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"🛒 Προσθήκη στο Καλάθι ({item['price']}€)", callback_data=f"addcart_{item['id']}")]
        ])
        
        # Αν η προσφορά έχει εικόνες/βίντεο, στείλε και το πρώτο αρχείο μαζί με το κείμενο, αλλιώς μόνο κείμενο
        if item['file_ids'] and len(item['file_ids']) > 0:
            if item['media_types'][0] == "photo":
                await message.answer_photo(photo=item['file_ids'][0], caption=f"{title}📝 {item['description']}", reply_markup=keyboard, parse_mode="Markdown")
            else:
                await message.answer_video(video=item['file_ids'][0], caption=f"{title}📝 {item['description']}", reply_markup=keyboard, parse_mode="Markdown")
        else:
            await message.answer(f"{title}📝 {item['description']}", reply_markup=keyboard, parse_mode="Markdown")

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
        await conn.execute("DELETE FROM promo_codes WHERE expires_at <= NOW();")
        promos = await conn.fetch("SELECT code, discount, bonus_tickets, expires_at FROM promo_codes;")
        
    if not promos:
        await message.answer("Δεν υπάρχουν διαθέσιμοι εκπτωτικοί κωδικοί αυτή τη στιγμή. Μείνετε συντονισμένοι!")
        return
        
    now = datetime.datetime.now()
    text = "🎟️ **Διαθέσιμοι Εκπτωτικοί Κωδικοί**\n\n"
    
    for p in promos:
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
    user_full_name = callback.from_user.full_name
    
    try:
        async with db_pool.acquire() as conn:
            cart_items = await conn.fetch("""
                SELECT m.id, m.description, m.price, m.file_ids, m.media_types, m.is_subscription, m.duration_months, m.is_custom
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
            
            points_earned = int(total_price * 5) 
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
                
                # --- ΔΙΑΧΕΙΡΙΣΗ ΣΥΝΔΡΟΜΩΝ ---
                has_subscription_bought = False
                total_months_added = 0
                
                for item in cart_items:
                    if item['is_subscription']:
                        has_subscription_bought = True
                        months = item['duration_months']
                        total_months_added += months
                        
                        existing_sub = await conn.fetchrow(
                            "SELECT expires_at FROM active_subscriptions WHERE telegram_id = $1;", 
                            user_id
                        )
                        
                        now = datetime.datetime.now()
                        if existing_sub and existing_sub['expires_at'] > now:
                            new_expiry = existing_sub['expires_at'] + datetime.timedelta(days=30 * months)
                            await conn.execute(
                                "UPDATE active_subscriptions SET expires_at = $1, user_full_name = $2, notified_expiry = FALSE WHERE telegram_id = $3;",
                                new_expiry, user_full_name, user_id
                            )
                        else:
                            new_expiry = now + datetime.timedelta(days=30 * months)
                            await conn.execute(
                                """INSERT INTO active_subscriptions (telegram_id, user_full_name, expires_at, notified_expiry) 
                                   VALUES ($1, $2, $3, FALSE)
                                   ON CONFLICT (telegram_id) DO UPDATE 
                                   SET expires_at = $3, user_full_name = $2, notified_expiry = FALSE;""",
                                user_id, user_full_name, new_expiry
                            )

                await conn.execute("DELETE FROM cart WHERE telegram_id = $1;", user_id)
                
        await state.update_data(promo_code=None, promo_discount=0, promo_tickets=0)
                
        await callback.answer("✅ Η αγορά ήταν επιτυχής!", show_alert=False)
        
        # --- CUSTOM ΠΡΟΪΟΝΤΑ & ΜΟΝΑΔΙΚΟΣ ΚΩΔΙΚΟΣ ---
        custom_items_bought = [item for item in cart_items if item.get('is_custom')]
        
        if custom_items_bought:
            order_code = generate_order_code()
            items_text = ", ".join([i['description'] for i in custom_items_bought])
            
            # Ειδοποίηση Admin
            for admin_id in ADMIN_IDS:
                try:
                    await bot.send_message(
                        admin_id, 
                        f"🔔 **ΝΕΑ CUSTOM ΠΑΡΑΓΓΕΛΙΑ!** 🔔\n\n👤 Από: {user_full_name} (`{user_id}`)\n🛒 Είδη: {items_text}\n🔑 Κωδικός: `#{order_code}`",
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass
            
            # Μήνυμα & Smart Link Χρήστη
            encoded_text = urllib.parse.quote(f"Γεια σου Δήμητρα! Αγόρασα custom παραγγελία. Ο κωδικός μου είναι #{order_code} και θέλω να συνεννοηθούμε για την κατασκευή.")
            smart_link = f"{ADMIN_LINK}?text={encoded_text}"
            
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="💬 Στείλε τον Κωδικό στην Admin", url=smart_link)
            ]])
            
            await bot.send_message(
                user_id,
                f"🎉 **Ευχαριστούμε για την παραγγελία του Custom Προϊόντος!**\n\nΟ μοναδικός κωδικός σου είναι: **#{order_code}**\n\nΠάτα το παρακάτω κουμπί για να μου στείλεις απευθείας τον κωδικό και να συνεννοηθούμε για το πώς θα το φτιάξω:",
                reply_markup=kb,
                parse_mode="Markdown"
            )
            
        elif has_subscription_bought:
            await callback.message.edit_text(
                f"✅ **Η συνδρομή ενεργοποιήθηκε επιτυχώς!**\n\n"
                f"🔗 Μπορείς να μπεις στην Premium ομάδα εδώ:\n{PREMIUM_GROUP_LINK}",
                parse_mode="Markdown"
            )
        else:
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
        
        # Αποστολή κανονικών αρχείων (αν υπήρχαν στο καλάθι και δεν είναι συνδρομές/custom)
        for item in cart_items:
            if item['is_subscription'] or item.get('is_custom'):
                continue
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
                    
            except Exception e:
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

# --- PROFILE HANDLERS ---
@dp.message(F.text == "👤 Το Προφίλ μου")
async def show_profile(message: types.Message):
    try:
        user_id = message.from_user.id
        async with db_pool.acquire() as conn:
            user = await conn.fetchrow("SELECT * FROM users WHERE telegram_id = $1;", user_id)
            sub_info = await conn.fetchrow("SELECT expires_at FROM active_subscriptions WHERE telegram_id = $1;", user_id)
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
        bar_string, next_level_string = get_level_progress(points)

        profile_text = (
            f"👤 **Το Προφίλ σου**\n\n"
            f"👛 **Υπόλοιπο:** {balance}€\n"
        )
        
        now = datetime.datetime.now()
        if sub_info and sub_info['expires_at'] > now:
            profile_text += f"⭐ **Premium Συνδρομή:** Ενεργή έως {sub_info['expires_at'].strftime('%d/%m/%Y %H:%M')}\n"
        else:
            profile_text += f"⭐ **Premium Συνδρομή:** Ανενεργή\n"

        profile_text += (
            f"⭐ **Πόντοι:** {points}\n"
            f"🎖️ **Βαθμίδα:** {level_title}\n"
            f"{bar_string}\n"
            f"📈 _{next_level_string}_\n\n"
            f"🎟️ **Εισιτήρια Κλήρωσης:** {tickets}\n\n"
            f"📜 **Πρόσφατες Αγορές:**\n"
        )

        if purchases:
            for p in purchases:
                profile_text += f"- {p['items_summary']} ({p['total_price']}€) στις {p['created_at'].strftime('%d/%m %H:%M')}\n"
        else:
            profile_text += "Δεν έχεις κάνει κάποια αγορά ακόμα."

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📊 Δες όλα τα Levels & Πόντους", callback_data="show_levels_info")]
        ])

        await message.answer(profile_text, parse_mode="Markdown", reply_markup=keyboard)
        
    except Exception as e:
        logging.error(f"Error in show_profile for user {message.from_user.id}: {e}")
        await message.answer("❌ Προέκυψε κάποιο πρόβλημα κατά την εμφάνιση του προφίλ σου. Δοκίμασε ξανά αργότερα.")

@dp.callback_query(F.data == "show_levels_info")
async def show_levels_info_callback(callback: CallbackQuery):
    user_id = callback.from_user.id
    
    async with db_pool.acquire() as conn:
        user_points = await conn.fetchval("SELECT lifetime_points FROM users WHERE telegram_id = $1;", user_id) or 0
        
    bar_string, next_level_string = get_level_progress(user_points)

    text = (
        "📊 **Βαθμίδες (Levels) & Πόντοι**\n\n"
        "Αυτά είναι τα διαθέσιμα επίπεδα που μπορείς να ξεκλειδώσεις μαζεύοντας πόντους από τις αγορές σου:\n\n"
        " **Πρωτάρης🐣🔞** (0 - 149 πόντοι)\n"
        " **Τολμηρός💋🔞** (150 - 299 πόντοι)\n"
        " **Ορεξάτος👀🔥🔞** (300 - 499 πόντοι)\n"
        " **Αφέντης💋👑🔞** (500 - 999 πόντοι)\n"
        " **VIP🔞❤️ ** (1000+ πόντοι)\n\n"
        f"⭐ Έχεις συγκεντρώσει: **{user_points} πόντους**.\n"
        f"{bar_string}\n"
        f"🎯 {next_level_string}"
    )

    await callback.message.answer(text, parse_mode="Markdown")
    await callback.answer()


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
    await message.answer("Είμαι η Δήμητρα Σαββίδη και είμαι 22 με πολλές καύλες . Στείλτε μου μήνυμα για παραπάνω υλικό μου❤️💋🔞")

# --- MAIN EXECUTION ---
async def main():
    logging.basicConfig(level=logging.INFO)
    await init_db()
    asyncio.create_task(check_subscriptions_loop())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())