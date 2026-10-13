from aiogram import Router, F, types
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto
from config import BTC_WALLET, ADMIN_IDS, PENDING_GRAPHIC_URL
from states import CardTopUp, PaySafeTopUp
from database import get_db
from utils import has_pending_request
import logging

router = Router()

# Βάλε εδώ τα links από τα screenshots/φωτογραφίες που δείχνουν πώς γίνεται η κατάθεση
HELP_PHOTO_1 = "https://dummyimage.com/600x400/1a1a1a/ffffff&text=Vima+1"
HELP_PHOTO_2 = "https://dummyimage.com/600x400/1a1a1a/ffffff&text=Vima+2"

@router.message(F.text == "👛 Wallet")
async def show_wallet(message: types.Message):
    user_id = message.from_user.id
    db_pool = await get_db()
    
    async with db_pool.acquire() as conn:
        user = await conn.fetchrow("SELECT balance, total_spent FROM users WHERE telegram_id = $1;", user_id)
        
        # Ανάκτηση των τελευταίων 5 καταθέσεων (ενώνοντας PaySafe και MoonPay)
        history = await conn.fetch("""
            (SELECT 'PaySafe' as method, amount, status, created_at FROM paysafe_requests WHERE telegram_id = $1)
            UNION ALL
            (SELECT 'MoonPay' as method, amount, status, created_at FROM moonpay_requests WHERE telegram_id = $1)
            ORDER BY created_at DESC LIMIT 5;
        """, user_id)
    
    if not user:
        await message.answer("Σφάλμα! Πληκτρολογήστε /start για να δημιουργηθεί το προφίλ σας.")
        return

    balance = user['balance']
    total_spent = user['total_spent']
    
    text = f"👛 **Το Πορτοφόλι μου**\n\n💰 Διαθέσιμο Υπόλοιπο: **{balance}€**\n🛍️ Συνολικά Έξοδα: **{total_spent}€**\n\n"
    
    if history:
        text += "📊 **Ιστορικό Τελευταίων Καταθέσεων:**\n"
        for row in history:
            status_icon = "⏳" if row['status'] == "pending" else "✅" if row['status'] == "accepted" else "❌"
            date_str = row['created_at'].strftime('%d/%m %H:%M')
            text += f"{status_icon} {row['method']}: **{row['amount']}€** ({date_str})\n"
    else:
        text += "📊 **Ιστορικό:** Δεν υπάρχουν καταθέσεις ακόμα.\n"
        
    text += "\nΕπίλεξε τρόπο κατάθεσης:"
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Αγορά με Κάρτα (MoonPay)", callback_data="card_start")],
        [InlineKeyboardButton(text="💶 Κατάθεση με PaySafe", callback_data="paysafe_start")],
        [InlineKeyboardButton(text="❓ Βοήθεια / Πώς κάνω κατάθεση;", callback_data="wallet_help")]
    ])
    await message.answer(text, reply_markup=keyboard, parse_mode="Markdown")

@router.callback_query(F.data == "wallet_help")
async def show_wallet_help(callback: CallbackQuery):
    # Στέλνει απευθείας τις φωτογραφίες-οδηγό
    media = [
        InputMediaPhoto(media=HELP_PHOTO_1, caption="Βήμα 1: Οδηγίες Κατάθεσης"),
        InputMediaPhoto(media=HELP_PHOTO_2, caption="Βήμα 2: Ολοκλήρωση Πληρωμής")
    ]
    await callback.message.answer_media_group(media=media)
    await callback.answer()

@router.callback_query(F.data == "cancel_deposit")
async def cancel_deposit_flow(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Η διαδικασία κατάθεσης ακυρώθηκε.", parse_mode="Markdown")
    await callback.answer("Ακυρώθηκε")

# --- CARD (MOONPAY) FLOW ---
@router.callback_query(F.data == "card_start")
async def card_start(callback: CallbackQuery, state: FSMContext):
    db_pool = await get_db()
    if await has_pending_request(callback.from_user.id, db_pool):
        await callback.message.answer_photo(
            photo=PENDING_GRAPHIC_URL,
            caption="⏳ **Εκκρεμεί ήδη ένα αίτημα κατάθεσης!**\n\nΠαρακαλώ περίμενε να εγκριθεί ή να απορριφθεί το προηγούμενο αίτημά σου.",
            parse_mode="Markdown"
        )
        await callback.answer()
        return

    await callback.message.answer("💶 **Πληκτρολόγησε το ποσό σε Ευρώ** που θέλεις να πληρώσεις με την κάρτα σου (π.χ. 50):", parse_mode="Markdown")
    await state.set_state(CardTopUp.waiting_for_amount)
    await callback.answer()

@router.message(CardTopUp.waiting_for_amount, F.text)
async def process_card_amount(message: types.Message, state: FSMContext):
    try:
        amount = float(message.text.replace(",", "."))
        if amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Μη έγκυρο ποσό. Σε παρακαλώ γράψε έναν αριθμό (π.χ. 50):")
        return
    
    await state.update_data(amount=amount)
    moonpay_url = f"https://buy.moonpay.com?currencyCode=btc&baseCurrencyCode=eur&baseCurrencyAmount={amount}&walletAddress={BTC_WALLET}"
    
    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌐 Πληρωμή στο MoonPay", url=moonpay_url)],
        [InlineKeyboardButton(text="❌ Ακύρωση", callback_data="cancel_deposit")]
    ])
    
    msg_text = (
        f"💳 **Πληρωμή με Κάρτα μέσω MoonPay**\n\n"
        f"💶 **Ποσό:** {amount}€\n\n"
        f"1️⃣ Πάτα το παρακάτω κουμπί για να μεταφερθείς στο ασφαλές περιβάλλον.\n"
        f"2️⃣ Ολοκλήρωσε την αγορά με την κάρτα σου (ή Apple/Google Pay).\n\n"
        f"📸 **Μόλις ολοκληρωθεί η συναλλαγή, βγάλε screenshot την επιβεβαίωση και στείλ' την μου εδώ σε φωτογραφία!**"
    )
    await message.answer(msg_text, reply_markup=cancel_kb, parse_mode="Markdown")
    await state.set_state(CardTopUp.waiting_for_screenshot)

@router.message(CardTopUp.waiting_for_screenshot, F.photo)
async def process_card_screenshot(message: types.Message, state: FSMContext):
    photo_file_id = message.photo[-1].file_id
    data = await state.get_data()
    amount = data.get("amount", 0.0)
    user_id = message.from_user.id
    db_pool = await get_db()
    
    async with db_pool.acquire() as conn:
        req_id = await conn.fetchval(
            "INSERT INTO moonpay_requests (telegram_id, amount, photo_file_id) VALUES ($1, $2, $3) RETURNING id;",
            user_id, amount, photo_file_id
        )
    
    admin_caption = (
        f"💳 **ΝΕΟ ΑΙΤΗΜΑ ΚΑΡΤΑΣ (MoonPay)** 💳\n\n"
        f"🆔 Αίτημα #: `{req_id}`\n"
        f"👤 Χρήστης ID: `{user_id}`\n"
        f"💰 Ποσό: **{amount}€**\n\n"
        f"⚠️ _Έλεγξε το πορτοφόλι σου αν μπήκαν τα Bitcoin πριν πατήσεις Accept._"
    )
    
    admin_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Accept", callback_data=f"mp_acc_{req_id}"),
            InlineKeyboardButton(text="❌ Reject", callback_data=f"mp_rej_{req_id}")
        ]
    ])
    
    for admin_id in ADMIN_IDS:
        try:
            await message.bot.send_photo(chat_id=admin_id, photo=photo_file_id, caption=admin_caption, reply_markup=admin_kb, parse_mode="Markdown")
        except Exception:
            pass
            
    await message.answer("✅ Το screenshot στάλθηκε επιτυχώς! Μόλις επιβεβαιώσουμε τη συναλλαγή, τα χρήματα θα μπουν στο πορτοφόλι σου.")
    await state.clear()


# --- PAYSAFE FLOW ---
@router.callback_query(F.data == "paysafe_start")
async def paysafe_start(callback: CallbackQuery, state: FSMContext):
    db_pool = await get_db()
    if await has_pending_request(callback.from_user.id, db_pool):
        await callback.message.answer_photo(
            photo=PENDING_GRAPHIC_URL,
            caption="⏳ **Εκκρεμεί ήδη ένα αίτημα κατάθεσης!**\n\nΠαρακαλώ περίμενε να εγκριθεί ή να απορριφθεί το προηγούμενο αίτημά σου.",
            parse_mode="Markdown"
        )
        await callback.answer()
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="5€", callback_data="ps_amt_5"),
            InlineKeyboardButton(text="10€", callback_data="ps_amt_10"),
            InlineKeyboardButton(text="25€", callback_data="ps_amt_25")
        ],
        [
            InlineKeyboardButton(text="50€", callback_data="ps_amt_50"),
            InlineKeyboardButton(text="100€", callback_data="ps_amt_100")
        ]
    ])

    await callback.message.answer("💶 **Επίλεξε το ποσό της PaySafe** που θέλεις να καταθέσεις:", reply_markup=keyboard, parse_mode="Markdown")
    await state.set_state(PaySafeTopUp.waiting_for_amount)
    await callback.answer()

@router.callback_query(PaySafeTopUp.waiting_for_amount, F.data.startswith("ps_amt_"))
async def process_paysafe_amount_callback(callback: CallbackQuery, state: FSMContext):
    amount = float(callback.data.split("_")[2])
    await state.update_data(amount=amount)
    
    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Ακύρωση Κατάθεσης", callback_data="cancel_deposit")]
    ])
    
    await callback.message.edit_text(
        f"💶 Επέλεξες ποσό: **{amount}€**\n\n🔢 **Τώρα γράψε τον 12-ψήφιο κωδικό της PaySafe σου:**", 
        reply_markup=cancel_kb,
        parse_mode="Markdown"
    )
    await state.set_state(PaySafeTopUp.waiting_for_code)
    await callback.answer()

@router.message(PaySafeTopUp.waiting_for_code, F.text)
async def process_paysafe_code(message: types.Message, state: FSMContext):
    code = message.text.strip().replace(" ", "")
    
    if len(code) != 12 or not code.isdigit():
        await message.answer("❌ ΛΑΘΟΣ! Ο κωδικός πρέπει να αποτελείται από **ακριβώς 12 νούμερα**.", parse_mode="Markdown")
        return
    
    data = await state.get_data()
    amount = data['amount']
    user_id = message.from_user.id
    db_pool = await get_db()
    
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
            await message.bot.send_message(admin_id, admin_text, reply_markup=admin_kb, parse_mode="Markdown")
        except Exception as e:
            logging.error(f"PaySafe sending to admin {admin_id} failed: {e}")
            
    await message.answer("✅ Το αίτημά σου στάλθηκε επιτυχώς! Μόλις ο διαχειριστής επιβεβαιώσει τον κωδικό, τα χρήματα θα μπουν στο πορτοφόλι σου.")
    await state.clear()