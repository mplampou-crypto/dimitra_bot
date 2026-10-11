from aiogram import Router, F, types
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from database import get_db
from utils import get_user_level_info, get_level_progress

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

# --- ΕΜΦΑΝΙΣΗ ΑΝΑ ΚΑΤΗΓΟΡΙΑ (Με αύξουσα ταξινόμηση) ---
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
        # Φέρνει μόνο όσα ΔΕΝ είναι Gallery, φιλτράρει ανά κατηγορία και ταξινομεί από το φθηνότερο στο ακριβότερο!
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

# --- ΝΕΑ ΛΕΙΤΟΥΡΓΙΑ: GALLERY ---
@router.message(F.text == "🖼️ Gallery")
async def show_gallery(message: types.Message):
    user_id = message.from_user.id
    db_pool = await get_db()
    
    async with db_pool.acquire() as conn:
        points = await conn.fetchval("SELECT lifetime_points FROM users WHERE telegram_id = $1;", user_id) or 0
        
    current_title, current_level_num = get_user_level_info(points)
    bar_string, next_level_string, progress_percent = get_level_progress(points)
    
    async with db_pool.acquire() as conn:
        # Ξεκλειδώνει ΜΟΝΟ όσα έχουν χαμηλότερο level, Ή το ίδιο level αλλά μικρότερο/ίσο % !
        gallery_items = await conn.fetch("""
            SELECT * FROM locked_media 
            WHERE is_gallery = TRUE 
            AND (unlock_level < $1 OR (unlock_level = $1 AND unlock_percentage <= $2))
            ORDER BY unlock_level ASC, unlock_percentage ASC;
        """, current_level_num, progress_percent)

    if gallery_items:
        await message.answer("🔓 **Το ξεκλειδωμένο σου υλικό:**", parse_mode="Markdown")
        for item in gallery_items:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=f"🛒 Προσθήκη ({item['price']}€)", callback_data=f"addcart_{item['id']}")]
            ])
            await message.answer(f"🖼️ {item['description']}", reply_markup=kb, parse_mode="Markdown")
    else:
        await message.answer("🔒 Δεν έχεις ξεκλειδώσει ακόμα κάποιο υλικό στην Gallery. Κάνε αγορές για να μαζέψεις πόντους!")

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

# Η προσθήκη στο καλάθι (addcart_...) θα μπει στο επόμενο αρχείο (cart.py) μαζί με το Checkout!