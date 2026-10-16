from aiogram import Router, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from config import ADMIN_IDS
from database import get_db
from states import UploadMedia, UploadCustom
from utils import LEVEL_THRESHOLDS

router = Router()

# --- 1. DASHBOARD & ΣΤΑΤΙΣΤΙΚΑ ---
@router.message(Command("admin"))
async def admin_dashboard(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
        
    db_pool = await get_db()
    async with db_pool.acquire() as conn:
        total_users = await conn.fetchval("SELECT COUNT(*) FROM users;")
        
        lvl0 = await conn.fetchval("SELECT COUNT(*) FROM users WHERE lifetime_points <= 200;")
        lvl1 = await conn.fetchval("SELECT COUNT(*) FROM users WHERE lifetime_points BETWEEN 201 AND 400;")
        lvl2 = await conn.fetchval("SELECT COUNT(*) FROM users WHERE lifetime_points BETWEEN 401 AND 650;")
        lvl3 = await conn.fetchval("SELECT COUNT(*) FROM users WHERE lifetime_points BETWEEN 651 AND 900;")
        lvl4 = await conn.fetchval("SELECT COUNT(*) FROM users WHERE lifetime_points >= 901;")
        
        paysafe_sum = float(await conn.fetchval("SELECT SUM(amount) FROM paysafe_requests WHERE status = 'accepted';") or 0.0)
        moonpay_sum = float(await conn.fetchval("SELECT SUM(amount) FROM moonpay_requests WHERE status = 'accepted';") or 0.0)
        total_revenue = paysafe_sum + moonpay_sum
        
    stats_text = (
        "👑 **Πίνακας Ελέγχου Διαχειριστή**\n\n"
        f"👥 **Συνολικοί Επισκέπτες:** {total_users}\n\n"
        "📊 **Χρήστες ανά Βαθμίδα:**\n"
        f"🐣 Πρωτάρης (0-200): {lvl0}\n"
        f"💋 Τολμηρός (201-400): {lvl1}\n"
        f"🔥 Ορεξάτος (401-650): {lvl2}\n"
        f"👑 Αφέντης (651-900): {lvl3}\n"
        f"❤️ Ultimate VIP (901+): {lvl4}\n\n"
        f"💰 **Πραγματικά Έσοδα:** {total_revenue:.2f}€\n"
        f"  ├ PaySafe: {paysafe_sum:.2f}€\n"
        f"  └ MoonPay: {moonpay_sum:.2f}€\n\n"
        "📜 **Διαθέσιμες Εντολές:**\n"
        "`/add_media` - Ανέβασμα Αρχείων (Κατάλογος / Gallery)\n"
        "`/add_custom` - Custom Προϊόν\n"
        "`/add_sub` - Συνδρομή Ομάδας\n"
        "`/set_giveaway` - Ρύθμιση κλήρωσης & δώρων\n"
        "`/add_promo` - Προσθήκη Κωδικού\n"
        "`/del_promo` - Διαγραφή Κωδικού\n"
        "`/remove_media` - Διαγραφή προσφοράς\n"
        "`/give_money` - Προσθήκη υπολοίπου σε χρήστη\n"
        "`/search <ID>` - Εύρεση χρήστη"
    )
    await message.answer(stats_text, parse_mode="Markdown")

# --- 2. ΔΙΑΧΕΙΡΙΣΗ PROMO CODES ---
@router.message(Command("add_promo"))
async def admin_add_promo(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
        
    args = message.text.split()
    if len(args) < 5 or len(args) > 6:
        await message.answer("⚠️ Χρήση: `/add_promo <code> <discount> <hours> <tickets> <min_level>`\nLevel: 0=Πρωτάρης, 1=Τολμηρός, 2=Ορεξάτος, 3=Αφέντης, 4=VIP", parse_mode="Markdown")
        return
        
    code = args[1].upper()
    try:
        discount = int(args[2])
        hours = float(args[3])
        tickets = int(args[4])
        min_level = int(args[5]) if len(args) == 6 else 0
    except ValueError:
        await message.answer("❌ Τα νούμερα πρέπει να είναι ακέραιοι (εκτός από τις ώρες).")
        return
        
    db_pool = await get_db()
    async with db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO promo_codes (code, discount, bonus_tickets, min_level, expires_at) 
            VALUES ($1, $2, $3, $4, NOW() + ($5 * INTERVAL '1 hour')) 
            ON CONFLICT (code) DO UPDATE SET discount = $2, bonus_tickets = $3, min_level = $4, expires_at = NOW() + ($5 * INTERVAL '1 hour');
        """, code, discount, tickets, min_level, hours)
        
    await message.answer(f"✅ Κωδικός **{code}** αποθηκεύτηκε!\nΈκπτωση: {discount}%, Δώρο: {tickets}🎟️\nΕλάχιστο Level: {min_level}\nΛήγει σε {hours} ώρες.", parse_mode="Markdown")

@router.message(Command("del_promo"))
async def admin_del_promo(message: types.Message):
    if message.from_user.id not in ADMIN_IDS: return
    args = message.text.split()
    if len(args) != 2:
        await message.answer("⚠️ Χρήση: `/del_promo <CODE>`", parse_mode="Markdown")
        return
    code = args[1].upper()
    db_pool = await get_db()
    async with db_pool.acquire() as conn:
        deleted = await conn.execute("DELETE FROM promo_codes WHERE code = $1;", code)
    if deleted == "DELETE 0":
        await message.answer(f"⚠️ Ο κωδικός {code} δεν βρέθηκε.")
    else:
        await message.answer(f"🗑️ Ο κωδικός {code} διαγράφηκε επιτυχώς.")

# --- 3. ΡΥΘΜΙΣΗ GIVEAWAY ---
@router.message(Command("set_giveaway"))
async def admin_set_giveaway(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return

    text = message.text.replace("/set_giveaway", "").strip()
    if not text or "|" not in text:
        await message.answer("⚠️ Χρήση: `/set_giveaway <hours> | <prize1> | <prize2> | ...`\nπ.χ. /set_giveaway 48 | 50€ PaySafe | Custom Video", parse_mode="Markdown")
        return

    parts = [p.strip() for p in text.split("|")]
    try:
        hours = float(parts[0].replace(",", "."))
    except ValueError:
        await message.answer("❌ Λάθος αριθμός ωρών.")
        return

    prizes_list = parts[1:]
    prizes_text = "\n".join([f"{i+1}️⃣ {p}" for i, p in enumerate(prizes_list)])

    db_pool = await get_db()
    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE giveaway_settings SET ends_at = NOW() + ($1 * INTERVAL '1 hour'), prizes = $2 WHERE id = 1;", hours, prizes_text)
        await conn.execute("UPDATE users SET giveaway_tickets = 0;")

    await message.answer(f"✅ Η κλήρωση ρυθμίστηκε! Λήγει σε {hours} ώρες.\n\n🎁 **Δώρα:**\n{prizes_text}")

# --- 4. UPLOAD MEDIA (ΚΑΤΑΛΟΓΟΣ Ή GALLERY) ---
@router.message(Command("add_media"))
async def start_upload(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
        
    await state.update_data(file_ids=[], media_types=[])
    await message.answer("📤 Στείλε μου **μία-μία** τις φωτογραφίες ή βίντεο.\nΜόλις τελειώσεις, γράψε `/done`")
    await state.set_state(UploadMedia.waiting_for_files)

@router.message(UploadMedia.waiting_for_files, F.photo | F.video)
async def receive_media_files(message: types.Message, state: FSMContext):
    data = await state.get_data()
    file_ids, media_types = data.get("file_ids", []), data.get("media_types", [])
    if message.photo:
        file_ids.append(message.photo[-1].file_id)
        media_types.append("photo")
    elif message.video:
        file_ids.append(message.video.file_id)
        media_types.append("video")
    await state.update_data(file_ids=file_ids, media_types=media_types)
    await message.answer(f"✅ Αρχεία: {len(file_ids)}. Στείλε κι άλλα ή γράψε `/done`.")

@router.message(UploadMedia.waiting_for_files, Command("done"))
async def ask_placement(message: types.Message, state: FSMContext):
    data = await state.get_data()
    if not data.get("file_ids"):
        await message.answer("⚠️ Δεν έστειλες αρχεία.")
        return
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛍️ Κατάλογος", callback_data="place_catalog")],
        [InlineKeyboardButton(text="🖼️ Gallery", callback_data="place_gallery")]
    ])
    await message.answer("Πού θέλεις να ανέβει αυτό το υλικό;", reply_markup=kb)
    await state.set_state(UploadMedia.waiting_for_placement)

@router.callback_query(UploadMedia.waiting_for_placement, F.data.startswith("place_"))
async def placement_decision(callback: CallbackQuery, state: FSMContext):
    choice = callback.data.split("_")[1]
    if choice == "catalog":
        await state.update_data(is_gallery=False)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💎 VIP", callback_data="cat_vip")],
            [InlineKeyboardButton(text="📸 Φωτό", callback_data="cat_photo"), InlineKeyboardButton(text="🎥 Βίντεο", callback_data="cat_video")]
        ])
        await callback.message.edit_text("Επίλεξε υποκατηγορία καταλόγου:", reply_markup=kb)
        await state.set_state(UploadMedia.waiting_for_catalog_cat)
    else:
        # Εδώ διορθώθηκε το πρόβλημα με τα βίντεο στην Gallery!
        data = await state.get_data()
        correct_category = "video" if "video" in data.get("media_types", []) else "photo"
        await state.update_data(is_gallery=True, category=correct_category)
        
        await callback.message.edit_text("Για ποιο Level θέλεις να είναι ορατό;\n0=Πρωτάρης, 1=Τολμηρός, 2=Ορεξάτος, 3=Αφέντης, 4=VIP")
        await state.set_state(UploadMedia.waiting_for_gallery_level)

@router.callback_query(UploadMedia.waiting_for_catalog_cat, F.data.startswith("cat_"))
async def process_cat(callback: CallbackQuery, state: FSMContext):
    # Αυτό αποθηκεύει σωστά "photo", "video" ή "vip" όπως ακριβώς το ψάχνει η βάση!
    await state.update_data(category=callback.data.split("_")[1], unlock_level=0, unlock_percentage=0)
    await callback.message.edit_text("✍️ Γράψε περιγραφή:")
    await state.set_state(UploadMedia.waiting_for_description)

@router.message(UploadMedia.waiting_for_gallery_level, F.text)
async def process_gal_level(message: types.Message, state: FSMContext):
    await state.update_data(unlock_level=int(message.text))
    await message.answer("Σε ποιο ΠΟΣΟΣΤΟ % του επιπέδου θέλεις να ξεκλειδώνει; (0-100)\nπ.χ. 0=Αμέσως, 50=Στη μέση του level.")
    await state.set_state(UploadMedia.waiting_for_gallery_perc)

@router.message(UploadMedia.waiting_for_gallery_perc, F.text)
async def process_gal_perc(message: types.Message, state: FSMContext):
    await state.update_data(unlock_percentage=int(message.text))
    await message.answer("✍️ Γράψε περιγραφή:")
    await state.set_state(UploadMedia.waiting_for_description)

@router.message(UploadMedia.waiting_for_description, F.text)
async def receive_desc(message: types.Message, state: FSMContext):
    data = await state.get_data()
    description = message.text
    await state.update_data(description=description)
    
    if data.get('is_gallery', False):
        db_pool = await get_db()
        async with db_pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO locked_media (file_ids, media_types, description, price, is_subscription, duration_months, is_custom, category, is_gallery, unlock_level, unlock_percentage) 
                   VALUES ($1, $2, $3, $4, FALSE, 0, FALSE, $5, $6, $7, $8)""",
                data['file_ids'], data['media_types'], description, 0.0, data.get('category', 'photo'), True, data.get('unlock_level', 0), data.get('unlock_percentage', 0)
            )
        await message.answer("✅ Το υλικό ανέβηκε στην Gallery! (Ρυθμίστηκε αυτόματα ως Δωρεάν ξεκλείδωμα).")
        await state.clear()
    else:
        await message.answer("💰 Τιμή (σε ευρώ):")
        await state.set_state(UploadMedia.waiting_for_price)

@router.message(UploadMedia.waiting_for_price, F.text)
async def receive_price(message: types.Message, state: FSMContext):
    data = await state.get_data()
    db_pool = await get_db()
    try:
        price = float(message.text.replace(",", "."))
    except ValueError:
        await message.answer("❌ Λάθος αριθμός. Ξαναγράψε την τιμή:")
        return
        
    async with db_pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO locked_media (file_ids, media_types, description, price, is_subscription, duration_months, is_custom, category, is_gallery, unlock_level, unlock_percentage) 
               VALUES ($1, $2, $3, $4, FALSE, 0, FALSE, $5, $6, $7, $8)""",
            data['file_ids'], data['media_types'], data['description'], price, data.get('category', 'photo'), False, 0, 0
        )
    await message.answer("✅ Το υλικό ανέβηκε στον Κατάλογο επιτυχώς!")
    await state.clear()

# --- 5. ΠΡΟΣΘΗΚΗ CUSTOM, ΣΥΝΔΡΟΜΗΣ & ΔΙΑΧΕΙΡΙΣΗ ΧΡΗΣΤΩΝ ---
@router.message(Command("add_custom"))
async def start_custom_upload(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    await state.update_data(file_ids=[], media_types=[])
    await message.answer("⌨️ **Δημιουργία Custom Προϊόντος**\n\n✍️ Γράψε την **περιγραφή**:", parse_mode="Markdown")
    await state.set_state(UploadCustom.waiting_for_description)

@router.message(UploadCustom.waiting_for_description, F.text)
async def receive_custom_description(message: types.Message, state: FSMContext):
    await state.update_data(description=message.text)
    await message.answer("💰 Όρισε την **τιμή** σε ευρώ (π.χ. 65.50):", parse_mode="Markdown")
    await state.set_state(UploadCustom.waiting_for_price)

@router.message(UploadCustom.waiting_for_price, F.text)
async def receive_custom_price(message: types.Message, state: FSMContext):
    try:
        price = float(message.text.replace(",", "."))
        if price <= 0: raise ValueError
    except ValueError:
        await message.answer("❌ Παρακαλώ γράψε έναν έγκυρο αριθμό.")
        return
        
    data = await state.get_data()
    db_pool = await get_db()
    async with db_pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO locked_media (file_ids, media_types, description, price, is_subscription, duration_months, is_custom, category) 
               VALUES ($1, $2, $3, $4, FALSE, 0, TRUE, 'vip')""",
            [], [], data['description'], price
        )
    await message.answer("✅ Το Custom Προϊόν ανέβηκε κατευθείαν στην κατηγορία VIP!")
    await state.clear()

@router.message(Command("add_sub"))
async def admin_add_sub(message: types.Message):
    if message.from_user.id not in ADMIN_IDS: return
    args = message.text.split(maxsplit=3)
    if len(args) < 4:
        await message.answer("⚠️ Χρήση: `/add_sub <τιμή> <μήνες> <περιγραφή>`", parse_mode="Markdown")
        return
    try:
        price = float(args[1].replace(",", "."))
        months = int(args[2])
        desc = args[3]
    except ValueError:
        await message.answer("❌ Λάθος μορφή αριθμών.")
        return
        
    db_pool = await get_db()
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO locked_media (description, price, is_subscription, duration_months, category) VALUES ($1, $2, TRUE, $3, 'sub')",
            desc, price, months
        )
    await message.answer(f"✅ Η συνδρομή ({months} μήνες - {price}€) προστέθηκε στην κατηγορία Συνδρομές!")

@router.message(Command("search"))
async def admin_search(message: types.Message):
    if message.from_user.id not in ADMIN_IDS: return
    args = message.text.split()
    if len(args) != 2:
        await message.answer("⚠️ Χρήση: `/search <ID>`", parse_mode="Markdown")
        return
    target_id = int(args[1])
    db_pool = await get_db()
    async with db_pool.acquire() as conn:
        user = await conn.fetchrow("SELECT instagram, tiktok, balance, lifetime_points, total_spent FROM users WHERE telegram_id = $1;", target_id)
    if not user:
        await message.answer("❌ Δεν βρέθηκε χρήστης.")
        return
    await message.answer(f"🔍 **Στοιχεία: {target_id}**\n📸 Instagram: {user['instagram']}\n🎵 TikTok: {user['tiktok']}\n\n💰 Υπόλοιπο: {user['balance']}€\n🛍️ Έξοδα: {user['total_spent']}€\n⭐ Πόντοι: {user['lifetime_points']}")

@router.message(Command("give_money"))
async def admin_give_money(message: types.Message):
    if message.from_user.id not in ADMIN_IDS: return
    args = message.text.split()
    if len(args) != 3:
        await message.answer("⚠️ Χρήση: `/give_money <ID> <Ποσό>`", parse_mode="Markdown")
        return
    target_id = int(args[1])
    amount = float(args[2].replace(",", "."))
    db_pool = await get_db()
    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE users SET balance = balance + $1 WHERE telegram_id = $2;", amount, target_id)
    await message.answer(f"✅ Προστέθηκαν {amount}€ στο πορτοφόλι του {target_id}!")

@router.message(Command("remove_media"))
async def admin_remove_media(message: types.Message):
    if message.from_user.id not in ADMIN_IDS: return
    db_pool = await get_db()
    async with db_pool.acquire() as conn:
        items = await conn.fetch("SELECT id, description, price, is_subscription, is_custom FROM locked_media;")
    if not items:
        await message.answer("Ο κατάλογος είναι άδειος!")
        return
    
    inline_keyboard = []
    for item in items:
        desc = item['description'][:30] + "..." if len(item['description']) > 30 else item['description']
        prefix = "⭐" if item['is_subscription'] else "⌨️" if item.get('is_custom') else "📦"
        inline_keyboard.append([InlineKeyboardButton(text=f"🗑️ {prefix} {desc} ({item['price']}€)", callback_data=f"admindel_{item['id']}")])
    
    await message.answer("🗑️ **Διαγραφή Προσφορών:**", reply_markup=InlineKeyboardMarkup(inline_keyboard=inline_keyboard), parse_mode="Markdown")

@router.callback_query(F.data.startswith("admindel_"))
async def process_admin_delete(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return
    media_id = int(callback.data.split("_")[1])
    db_pool = await get_db()
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM cart WHERE media_id = $1;", media_id)
        await conn.execute("DELETE FROM locked_media WHERE id = $1;", media_id)
    await callback.answer("✅ Διαγράφηκε οριστικά!", show_alert=True)
    await callback.message.delete()

# --- 6. ΕΓΚΡΙΣΗ / ΑΠΟΡΡΙΨΗ ΚΑΤΑΘΕΣΕΩΝ (ADMIN) ---

@router.callback_query(F.data.startswith("ps_acc_"))
async def admin_accept_paysafe(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return
    req_id = int(callback.data.split("_")[2])
    db_pool = await get_db()
    
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT telegram_id, amount, status FROM paysafe_requests WHERE id = $1;", req_id)
        if not row or row['status'] != 'pending':
            await callback.answer("❌ Μη έγκυρο ή ήδη ολοκληρωμένο.", show_alert=True)
            return
            
        await conn.execute("UPDATE paysafe_requests SET status = 'accepted' WHERE id = $1;", req_id)
        await conn.execute("UPDATE users SET balance = balance + $1 WHERE telegram_id = $2;", row['amount'], row['telegram_id'])
        
    await callback.message.edit_text(callback.message.text + f"\n\n✅ **ΑΠΟΔΕΚΤΟ από {callback.from_user.first_name}!**")
    try:
        await callback.message.bot.send_message(row['telegram_id'], f"🎉 Η κατάθεση PaySafe εγκρίθηκε! Προστέθηκαν {row['amount']}€.")
    except Exception:
        pass
    await callback.answer("Εγκρίθηκε!")

@router.callback_query(F.data.startswith("ps_rej_"))
async def admin_reject_paysafe(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return
    req_id = int(callback.data.split("_")[2])
    db_pool = await get_db()
    
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT telegram_id, status FROM paysafe_requests WHERE id = $1;", req_id)
        if not row or row['status'] != 'pending':
            await callback.answer("❌ Μη έγκυρο ή ήδη ολοκληρωμένο.", show_alert=True)
            return
        await conn.execute("UPDATE paysafe_requests SET status = 'rejected' WHERE id = $1;", req_id)
        
    await callback.message.edit_text(callback.message.text + f"\n\n❌ **ΑΠΟΡΡΙΦΘΗΚΕ από {callback.from_user.first_name}!**")
    try:
        await callback.message.bot.send_message(row['telegram_id'], "❌ Το αίτημα PaySafe απορρίφθηκε.")
    except Exception:
        pass
    await callback.answer("Απορρίφθηκε!")

@router.callback_query(F.data.startswith("mp_acc_"))
async def admin_accept_moonpay(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return
    req_id = int(callback.data.split("_")[2])
    db_pool = await get_db()
    
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT telegram_id, amount, status FROM moonpay_requests WHERE id = $1;", req_id)
        if not row or row['status'] != 'pending':
            await callback.answer("❌ Μη έγκυρο ή ήδη ολοκληρωμένο.", show_alert=True)
            return
            
        await conn.execute("UPDATE moonpay_requests SET status = 'accepted' WHERE id = $1;", req_id)
        await conn.execute("UPDATE users SET balance = balance + $1 WHERE telegram_id = $2;", row['amount'], row['telegram_id'])
        
    await callback.message.edit_caption(caption=callback.message.caption + f"\n\n✅ **ΑΠΟΔΕΚΤΟ από {callback.from_user.first_name}!**")
    try:
        await callback.message.bot.send_message(row['telegram_id'], f"🎉 Η κατάθεση MoonPay εγκρίθηκε! Προστέθηκαν {row['amount']}€.")
    except Exception:
        pass
    await callback.answer("Εγκρίθηκε!")

@router.callback_query(F.data.startswith("mp_rej_"))
async def admin_reject_moonpay(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return
    req_id = int(callback.data.split("_")[2])
    db_pool = await get_db()
    
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT telegram_id, status FROM moonpay_requests WHERE id = $1;", req_id)
        if not row or row['status'] != 'pending':
            await callback.answer("❌ Μη έγκυρο ή ήδη ολοκληρωμένο.", show_alert=True)
            return
        await conn.execute("UPDATE moonpay_requests SET status = 'rejected' WHERE id = $1;", req_id)
        
    await callback.message.edit_caption(caption=callback.message.caption + f"\n\n❌ **ΑΠΟΡΡΙΦΘΗΚΕ από {callback.from_user.first_name}!**")
    try:
        await callback.message.bot.send_message(row['telegram_id'], "❌ Το αίτημα MoonPay απορρίφθηκε.")
    except Exception:
        pass
    await callback.answer("Απορρίφθηκε!")