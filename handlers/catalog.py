from aiogram import Router, F, types
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from database import get_db
from utils import get_user_level_info, get_level_progress, LEVEL_THRESHOLDS

router = Router()

# --- ΚΕΝΤΡΙΚΟ ΜΕΝΟΥ ΚΑΤΑΛΟΓΟΥ ---
@router.message(F.text == "🛍️ Catalog")
async def show_catalog_main(message: types.Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ Συνδρομές", callback_data="cat_sub")],
        [InlineKeyboardButton(text="💎 VIP (Customs)", callback_data="cat_vip")],
        [InlineKeyboardButton(text="📸 Φωτογραφίες", callback_data="cat_photo")],
        [InlineKeyboardButton(text="🎥 Βίντεο", callback_data="cat_video")]
    ])
    await message.answer("🛍️ **Επίλεξε κατηγορία καταλόγου:**", reply_markup=keyboard, parse_mode="Markdown")

# --- ΕΜΦΑΝΙΣΗ ΑΝΑ ΚΑΤΗΓΟΡΙΑ ---
@router.callback_query(F.data.startswith("cat_"))
async def show_category_items(callback: CallbackQuery):
    category = callback.data.split("_")[1]
    db_pool = await get_db()
    
    category_names = {
        "sub": "⭐ Συνδρομές VIP",
        "vip": "💎 VIP Customs",
        "photo": "📸 Φωτογραφίες",
        "video": "🎥 Βίντεο"
    }
    title = category_names.get(category, "Κατάλογος")
    
    async with db_pool.acquire() as conn:
        items = await conn.fetch(
            "SELECT * FROM locked_media WHERE is_gallery = FALSE AND category = $1 ORDER BY price ASC;", 
            category
        )
        
    if not items:
        await callback.answer("Αυτή η κατηγορία είναι άδεια προς το παρόν!", show_alert=True)
        return
        
    await callback.message.answer(f"Άνοιξες την κατηγορία: **{title}**\nΕδώ εμφανίζονται από το φθηνότερο προς το ακριβότερο!", parse_mode="Markdown")
    
    for item in items:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"🛒 Προσθήκη στο Καλάθι ({item['price']}€)", callback_data=f"addcart_{item['id']}")]
        ])
        
        item_title = f"📦 **{title}**" if category != "sub" else f"⭐ **Συνδρομή** ({item['duration_months']} Μήνες)"
        await callback.message.answer(f"{item_title}\n📝 {item['description']}", reply_markup=kb, parse_mode="Markdown")
    
    await callback.answer()

# --- GALLERY (Με Εμφάνιση Κλειδωμένων) ---
@router.message(F.text == "🖼️ Gallery")
async def show_gallery(message: types.Message):
    user_id = message.from_user.id
    db_pool = await get_db()
    
    async with db_pool.acquire() as conn:
        points = await conn.fetchval("SELECT lifetime_points FROM users WHERE telegram_id = $1;", user_id) or 0
        
    current_title, current_level_num = get_user_level_info(points)
    bar_string, next_level_string, progress_percent = get_level_progress(points)
    
    async with db_pool.acquire() as conn:
        # Φέρνουμε ΟΛΑ τα αντικείμενα της Gallery
        gallery_items = await conn.fetch("""
            SELECT * FROM locked_media 
            WHERE is_gallery = TRUE 
            ORDER BY unlock_level ASC, unlock_percentage ASC;
        """)

    if gallery_items:
        await message.answer("🖼️ **Η Gallery της Δήμητρας:**\n_Εδώ βλέπεις όλο το υλικό! Κάνε αγορές για να το ξεκλειδώσεις._", parse_mode="Markdown")
        for item in gallery_items:
            req_lvl = item['unlock_level']
            req_perc = item['unlock_percentage']
            
            # Βρίσκουμε το όνομα του απαιτούμενου Level
            req_title = "Άγνωστο"
            for _, _, title, lvl_num in LEVEL_THRESHOLDS:
                if lvl_num == req_lvl:
                    req_title = title
                    break
            
            # Ελέγχουμε αν ο χρήστης έχει πρόσβαση
            is_unlocked = False
            if current_level_num > req_lvl:
                is_unlocked = True
            elif current_level_num == req_lvl and progress_percent >= req_perc:
                is_unlocked = True
            
            if is_unlocked:
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text=f"🛒 Προσθήκη ({item['price']}€)", callback_data=f"addcart_{item['id']}")]
                ])
                await message.answer(f"🔓 **{item['description']}**\n_(Ξεκλείδωτο)_", reply_markup=kb, parse_mode="Markdown")
            else:
                await message.answer(f"🔒 **{item['description']}**\n⚠️ _Απαιτείται: {req_title} & {req_perc}% της μπάρας_", parse_mode="Markdown")
    else:
        await message.answer("Δεν υπάρχει ακόμα υλικό στην Gallery.")

    # Εμφάνιση των Πόντων, του Progress Bar και του κειμένου με τα Δώρα
    perks_text = (
        f"\n\n🎖️ **Το Επίπεδό σου:** {current_title}\n"
        f"⭐ **Πόντοι:** {points}\n"
        f"{bar_string}\n"
        f"📈 _{next_level_string}_\n\n"
        "🏆 **Βαθμίδες (Levels) & Στάδια**\n"
        "Ανέβασε επίπεδο μαζεύοντας πόντους από τις αγορές σου (10€ = 50 πόντοι):\n\n"
        "🐣 **Πρωτάρης🐣🔞** (0 - 200 πόντοι)\n"
        "💋 **Τολμηρός💋🔞** (201 - 400 πόντοι) ➡️ **Δώρο:** +1 bundle 10 hot photos +5€ bot token\n"
        "🔥 **Ορεξάτος👀🔥🔞** (401 - 650 πόντοι) ➡️ **Δώρο:** +30€ bot token\n"
        "👑 **Αφέντης💋👑🔞** (651 - 900 πόντοι) ➡️ **Δώρο:** +1 custom video (+10 m 🔥🔥) +5 hot video🔞 +10 bot token\n"
        "❤️ **Ultimate VIP❤️🔞** (901+ πόντοι) ➡️ **Δώρο:** +1 FaceTime (+30 m) +3 hottest videos🔞 +10 nudes photos\n\n"
        "🎁 **Όροι & Εξαργύρωση:**\n"
        "• Μπορείς να πάρεις την ανταμοιβή σου αποκλειστικά όταν φτάσεις στο αντίστοιχο level.\n"
        "• Μόλις το φτάσεις, στείλε προσωπικό μήνυμα στην Dimitra για να το εξαργυρώσεις!"
    )
    
    await message.answer(perks_text, parse_mode="Markdown")