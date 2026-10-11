import datetime
import urllib.parse
from decimal import Decimal
from aiogram import Router, F, types
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto, InputMediaVideo
from aiogram.fsm.context import FSMContext

from states import CartPromo
from database import get_db
from config import ADMIN_LINK, PREMIUM_GROUP_LINK
from utils import generate_order_code, get_user_level_info
import logging

router = Router()

async def get_cart_text_and_keyboard(user_id: int, state: FSMContext):
    db_pool = await get_db()
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
        
    inline_keyboard = []
    for item in cart_items:
        inline_keyboard.append([InlineKeyboardButton(text=f"❌ Αφαίρεση {item['description']}", callback_data=f"removecart_{item['cart_id']}")])
    
    if promo_discount > 0 or promo_tickets > 0:
        inline_keyboard.append([InlineKeyboardButton(text=f"❌ Αφαίρεση Κωδικού ({promo_code})", callback_data="remove_promo")])
    else:
        inline_keyboard.append([InlineKeyboardButton(text="🎟️ Προσθήκη Κωδικού Έκπτωσης", callback_data="ask_promo")])
        
    inline_keyboard.append([InlineKeyboardButton(text=f"💳 Αγορά Όλων ({total_price:.2f}€)", callback_data="checkout")])
    inline_keyboard.append([InlineKeyboardButton(text="🗑 Άδειασμα Καλαθιού", callback_data="clearcart")])

    return text, InlineKeyboardMarkup(inline_keyboard=inline_keyboard)

@router.callback_query(F.data.startswith("addcart_"))
async def process_add_to_cart(callback: CallbackQuery):
    media_id = int(callback.data.split("_")[1])
    user_id = callback.from_user.id
    db_pool = await get_db()
    
    async with db_pool.acquire() as conn:
        exists = await conn.fetchval("SELECT 1 FROM cart WHERE telegram_id = $1 AND media_id = $2;", user_id, media_id)
        if exists:
            await callback.answer("⚠️ Το έχεις ήδη προσθέσει στο καλάθι σου!", show_alert=True)
            return
        await conn.execute("INSERT INTO cart (telegram_id, media_id) VALUES ($1, $2);", user_id, media_id)
        
    await callback.answer("✅ Προστέθηκε στο καλάθι!", show_alert=False)

@router.message(F.text == "🛒 Cart")
async def show_cart(message: types.Message, state: FSMContext):
    text, kb = await get_cart_text_and_keyboard(message.from_user.id, state)
    if kb:
        await message.answer(text, reply_markup=kb, parse_mode="Markdown")
    else:
        await message.answer(text)

@router.callback_query(F.data.startswith("removecart_"))
async def process_remove_from_cart(callback: CallbackQuery, state: FSMContext):
    cart_id = int(callback.data.split("_")[1])
    user_id = callback.from_user.id
    db_pool = await get_db()
    
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM cart WHERE id = $1 AND telegram_id = $2;", cart_id, user_id)
        
    await callback.answer("❌ Το προϊόν αφαιρέθηκε από το καλάθι!", show_alert=False)
    text, kb = await get_cart_text_and_keyboard(user_id, state)
    if kb:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")
    else:
        await callback.message.edit_text(text, parse_mode="Markdown")

@router.callback_query(F.data == "clearcart")
async def process_clear_cart(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    db_pool = await get_db()
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM cart WHERE telegram_id = $1;", user_id)
        
    await state.update_data(promo_code=None, promo_discount=0, promo_tickets=0)
    await callback.answer("🗑 Το καλάθι σου άδειασε!", show_alert=True)
    await callback.message.edit_text("🛒 Το καλάθι σου είναι πλέον άδειο.")

# --- PROMO CODES ---
@router.message(F.text == "🎟️ Promo Codes")
async def show_promo_codes(message: types.Message):
    user_id = message.from_user.id
    db_pool = await get_db()
    
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM promo_codes WHERE expires_at <= NOW();")
        promos = await conn.fetch("SELECT code, discount, bonus_tickets, expires_at, min_level FROM promo_codes;")
        points = await conn.fetchval("SELECT lifetime_points FROM users WHERE telegram_id = $1;", user_id) or 0
        
    _, current_level_num = get_user_level_info(points)
    now = datetime.datetime.now()
    
    # Φιλτράρισμα κωδικών βάσει level
    visible_promos = [p for p in promos if current_level_num >= p['min_level']]
    
    if not visible_promos:
        await message.answer("Δεν υπάρχουν διαθέσιμοι εκπτωτικοί κωδικοί για το επίπεδό σου αυτή τη στιγμή!")
        return
        
    text = "🎟️ **Διαθέσιμοι Εκπτωτικοί Κωδικοί**\n\n"
    for p in visible_promos:
        remaining = p['expires_at'] - now
        hours, remainder = divmod(int(remaining.total_seconds()), 3600)
        minutes, _ = divmod(remainder, 60)
        
        text += f"🔹 Κωδικός: `{p['code']}`\n   ➖ Έκπτωση: **{p['discount']}%**\n"
        if p['bonus_tickets'] > 0:
            text += f"   🎁 Δώρο: **+{p['bonus_tickets']} Εισιτήρια**\n"
        text += f"   ⏳ Λήγει σε: **{hours} ώρες και {minutes} λεπτά**\n\n"
        
    await message.answer(text, parse_mode="Markdown")

@router.callback_query(F.data == "ask_promo")
async def ask_promo_code(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("🎟 Στείλε μου στο chat τον κωδικό έκπτωσης που διαθέτεις:")
    await state.set_state(CartPromo.waiting_for_promo)
    await callback.answer()

@router.message(CartPromo.waiting_for_promo, F.text)
async def apply_promo_code(message: types.Message, state: FSMContext):
    code = message.text.strip().upper()
    user_id = message.from_user.id
    db_pool = await get_db()
    
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM promo_codes WHERE expires_at <= NOW();")
        promo = await conn.fetchrow("SELECT discount, bonus_tickets, min_level FROM promo_codes WHERE code = $1;", code)
        points = await conn.fetchval("SELECT lifetime_points FROM users WHERE telegram_id = $1;", user_id) or 0
        
    _, current_level_num = get_user_level_info(points)
    
    if promo:
        if current_level_num < promo['min_level']:
            await message.answer("❌ Αυτός ο κωδικός είναι αποκλειστικά για χρήστες υψηλότερης βαθμίδας (VIP)!")
        else:
            async with db_pool.acquire() as conn:
                already_used = await conn.fetchval("SELECT 1 FROM user_promo_usage WHERE telegram_id = $1 AND promo_code = $2;", user_id, code)
                
            if already_used:
                await message.answer("❌ Έχεις ήδη χρησιμοποιήσει αυτόν τον εκπτωτικό κωδικό.")
            else:
                await state.update_data(promo_code=code, promo_discount=promo['discount'], promo_tickets=promo['bonus_tickets'])
                msg = f"✅ Ο κωδικός **{code}** εφαρμόστηκε! Κέρδισες **-{promo['discount']}%** έκπτωση."
                if promo['bonus_tickets'] > 0:
                    msg += f" Και **+{promo['bonus_tickets']} εισιτήρια**!"
                await message.answer(msg, parse_mode="Markdown")
    else:
        await message.answer("❌ Ο κωδικός που έβαλες είναι άκυρος ή έχει λήξει.")
            
    await state.set_state(None)
    text, kb = await get_cart_text_and_keyboard(user_id, state)
    if kb:
        await message.answer(text, reply_markup=kb, parse_mode="Markdown")

@router.callback_query(F.data == "remove_promo")
async def remove_promo_code(callback: CallbackQuery, state: FSMContext):
    await state.update_data(promo_code=None, promo_discount=0, promo_tickets=0)
    await callback.answer("🗑️ Ο κωδικός αφαιρέθηκε.", show_alert=False)
    text, kb = await get_cart_text_and_keyboard(callback.from_user.id, state)
    if kb:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")

# --- CHECKOUT ---
@router.callback_query(F.data == "checkout")
async def process_checkout(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    user_full_name = callback.from_user.full_name
    db_pool = await get_db()
    
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
            promo_code = data.get("promo_code", None)
            
            total_price = Decimal(str(original_price))
            if promo_discount > 0:
                multiplier = Decimal(str(1 - promo_discount / 100.0))
                total_price = (total_price * multiplier).quantize(Decimal('0.01'))
                
            user = await conn.fetchrow("SELECT balance, lifetime_points FROM users WHERE telegram_id = $1;", user_id)
            
            if user['balance'] < total_price:
                await callback.answer(f"❌ Δεν έχεις αρκετό υπόλοιπο! (Χρειάζεσαι {total_price}€)\nΠήγαινε στο Πορτοφόλι.", show_alert=True)
                return
                
            new_balance = user['balance'] - total_price
            
            # Υπολογισμός νέων πόντων: 10€ = 50 πόντοι, άρα 1€ = 5 πόντοι
            points_earned = int(float(total_price) * 5) 
            old_points = user['lifetime_points']
            new_total_points = old_points + points_earned
            
            now = datetime.datetime.now()
            giveaway = await conn.fetchrow("SELECT ends_at FROM giveaway_settings WHERE id = 1;")
            
            tickets_to_add = promo_tickets
            if giveaway and giveaway['ends_at'] > now:
                # 50 πόντοι = 1 εισιτήριο
                tickets_to_add += (new_total_points // 50) - (old_points // 50)
            
            items_summary = ", ".join([item['description'] for item in cart_items])

            async with conn.transaction():
                await conn.execute(
                    """UPDATE users 
                       SET balance = $1, lifetime_points = lifetime_points + $2, 
                           giveaway_tickets = giveaway_tickets + $3, total_spent = total_spent + $4 
                       WHERE telegram_id = $5;""",
                    new_balance, points_earned, tickets_to_add, total_price, user_id
                )
                await conn.execute("INSERT INTO purchases (telegram_id, items_summary, total_price) VALUES ($1, $2, $3);", user_id, f"Αγορά: {items_summary}", total_price)
                if promo_code:
                    await conn.execute("INSERT INTO user_promo_usage (telegram_id, promo_code) VALUES ($1, $2) ON CONFLICT DO NOTHING;", user_id, promo_code)
                
                has_subscription_bought = False
                for item in cart_items:
                    if item['is_subscription']:
                        has_subscription_bought = True
                        months = item['duration_months']
                        existing_sub = await conn.fetchrow("SELECT expires_at FROM active_subscriptions WHERE telegram_id = $1;", user_id)
                        if existing_sub and existing_sub['expires_at'] > now:
                            new_expiry = existing_sub['expires_at'] + datetime.timedelta(days=30 * months)
                            await conn.execute("UPDATE active_subscriptions SET expires_at = $1, user_full_name = $2, notified_expiry = FALSE WHERE telegram_id = $3;", new_expiry, user_full_name, user_id)
                        else:
                            new_expiry = now + datetime.timedelta(days=30 * months)
                            await conn.execute("""INSERT INTO active_subscriptions (telegram_id, user_full_name, expires_at, notified_expiry) VALUES ($1, $2, $3, FALSE)
                                                  ON CONFLICT (telegram_id) DO UPDATE SET expires_at = $3, user_full_name = $2, notified_expiry = FALSE;""", user_id, user_full_name, new_expiry)

                await conn.execute("DELETE FROM cart WHERE telegram_id = $1;", user_id)
                
        await state.update_data(promo_code=None, promo_discount=0, promo_tickets=0)
        await callback.message.edit_text("✅ **Η παραγγελία ολοκληρώθηκε με επιτυχία!**", parse_mode="Markdown")
        
        custom_items_bought = [item for item in cart_items if item.get('is_custom')]
        if custom_items_bought:
            order_code = generate_order_code()
            encoded_text = urllib.parse.quote(f"Γεια σου Δήμητρα! Ο κωδικός μου είναι #{order_code}.")
            kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💬 Στείλε τον Κωδικό στην Dimitra", url=f"{ADMIN_LINK}?text={encoded_text}")]])
            await callback.message.bot.send_message(user_id, f"🎉 Ο μοναδικός κωδικός σου για τα Customs είναι: **#{order_code}**\nΣτείλε μου τον κωδικό 💋❤️:", reply_markup=kb, parse_mode="Markdown")
            
        if has_subscription_bought:
            sub_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💎 Είσοδος στο VIP Group", url=PREMIUM_GROUP_LINK)]])
            await callback.message.bot.send_message(user_id, "✅ Η συνδρομή σου ενεργοποιήθηκε!\nΜπες στην Premium ομάδα:", reply_markup=sub_kb)

        if tickets_to_add > 0:
            await callback.message.bot.send_message(user_id, f"🎉 Πήρες **{tickets_to_add} εισιτήρια** για την κλήρωση!", parse_mode="Markdown")
        
        for item in cart_items:
            if item['is_subscription'] or item.get('is_custom'):
                continue
            file_ids = item['file_ids']
            media_types = item['media_types']
            if len(file_ids) == 1:
                if media_types[0] == "photo":
                    await callback.message.bot.send_photo(chat_id=user_id, photo=file_ids[0], caption=f"🎉 {item['description']}")
                else:
                    await callback.message.bot.send_video(chat_id=user_id, video=file_ids[0], caption=f"🎉 {item['description']}")
            else:
                media_group = [InputMediaPhoto(media=f_id, caption=f"🎉 {item['description']}" if i==0 else None) if m_type == "photo" else InputMediaVideo(media=f_id, caption=f"🎉 {item['description']}" if i==0 else None) for i, (f_id, m_type) in enumerate(zip(file_ids, media_types))]
                await callback.message.bot.send_media_group(chat_id=user_id, media=media_group)
                
    except Exception as e:
        logging.error(f"Error during checkout for {user_id}: {e}")
        await callback.answer("❌ Προέκυψε σφάλμα. Δοκιμάστε ξανά.", show_alert=True)