import datetime
from aiogram import Router, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

# Σημείωση: Αφαιρέθηκε το UserOnboarding από εδώ γιατί δεν το χρειαζόμαστε πια
from keyboards import main_menu, support_keyboard
from database import get_db
from utils import get_user_level_info

router = Router()

@router.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    # Καθαρίζουμε τυχόν "κολλημένες" καταστάσεις
    await state.clear()
    
    user_id = message.from_user.id
    db_pool = await get_db()
    
    # Εγγράφουμε τον χρήστη κατευθείαν. Βάζουμε 'Παραλείφθηκε' στα social 
    # για να μην υπάρχει κενό στη βάση δεδομένων σου.
    async with db_pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO users (telegram_id, instagram, tiktok) 
               VALUES ($1, 'Παραλείφθηκε', 'Παραλείφθηκε') 
               ON CONFLICT (telegram_id) DO NOTHING;""",
            user_id
        )
        
    await message.answer(
        f"Καλωσόρισες {message.from_user.first_name}! 💋\n\n"
        "Χρησιμοποίησε το μενού παρακάτω για να πλοηγηθείς στις επιλογές μου και να ανακαλύψεις τον κατάλογο:",
        reply_markup=main_menu()
    )

@router.message(F.text == "ℹ️ Info & Support")
async def show_info(message: types.Message):
    text = (
        "Καλώς ήρθες! Είμαι η Δήμητρα, 22 χρόνων, με βάση τη Θεσσαλονίκη. Χαίρομαι που με βρήκες.\n\n"
        "Εδώ μέσα μπορείς να ανακαλύψεις, να ξεκλειδώσεις και να αγοράσεις αυτόματα το πιο ξεχωριστό μου υλικό. "
        "Περιηγήσου στο μενού παρακάτω για να δεις τις επιλογές.\n\n"
        "Αν θες να τα πούμε ή αν χρειάζεσαι βοήθεια με τις καταθέσεις, πάτα ένα από τα παρακάτω κουμπιά! Σε περιμένω... ❤️💋🔞"
    )
    await message.answer(text, reply_markup=support_keyboard())

@router.message(F.text == "👤 My Profile")
async def show_profile(message: types.Message):
    user_id = message.from_user.id
    db_pool = await get_db()
    
    async with db_pool.acquire() as conn:
        user = await conn.fetchrow("SELECT * FROM users WHERE telegram_id = $1;", user_id)
        sub_info = await conn.fetchrow("SELECT expires_at FROM active_subscriptions WHERE telegram_id = $1;", user_id)
        purchases = await conn.fetch(
            "SELECT items_summary, total_price, created_at FROM purchases WHERE telegram_id = $1 ORDER BY created_at DESC LIMIT 5;",
            user_id
        )

    if not user:
        await message.answer("Δεν βρέθηκαν στοιχεία προφίλ. Πληκτρολόγησε /start.")
        return

    balance = user['balance']
    points = user['lifetime_points']
    tickets = user['giveaway_tickets']
    
    level_title, _ = get_user_level_info(points)

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
        f"🎖️ **Βαθμίδα:** {level_title}\n\n"
        f"🎟️ **Εισιτήρια Κλήρωσης:** {tickets}\n\n"
        f"📜 **Πρόσφατες Αγορές:**\n"
    )

    if purchases:
        for p in purchases:
            profile_text += f"- {p['items_summary']} ({p['total_price']}€) στις {p['created_at'].strftime('%d/%m %H:%M')}\n"
    else:
        profile_text += "Δεν έχεις κάνει κάποια αγορά ακόμα."

    await message.answer(profile_text, parse_mode="Markdown")

@router.message(F.text == "🎁 Giveaway")
async def show_giveaway(message: types.Message):
    db_pool = await get_db()
    
    async with db_pool.acquire() as conn:
        settings = await conn.fetchrow("SELECT ends_at, prizes FROM giveaway_settings WHERE id = 1;")
        now = datetime.datetime.now()
        
        if settings and settings['ends_at'] <= now:
            text = (
                f"🎁 **Μεγάλη Κλήρωση**\n\n"
                f"🏁 **Η προηγούμενη κλήρωση έχει λήξει!**\n"
                f"⏳ Σύντομα θα ξεκινήσει καινούρια. Μείνε συντονισμένος!\n\n"
                f"💡 *Κάθε 10€ αγορών (50 πόντοι) σου εξασφαλίζουν αυτόματα 1 εισιτήριο!*"
            )
        else:
            total_tickets = await conn.fetchval("SELECT SUM(giveaway_tickets) FROM users;") or 0
            
            remaining_time = settings['ends_at'] - now
            hours, remainder = divmod(int(remaining_time.total_seconds()), 3600)
            minutes, _ = divmod(remainder, 60)
            
            prizes_text = settings['prizes'] if settings['prizes'] else "Τα δώρα δεν έχουν ανακοινωθεί ακόμα!"

            text = (
                f"🎁 **Μεγάλη Κλήρωση**\n\n"
                f"🎟️ **Συνολικά Εισιτήρια που έχουν δοθεί:** {total_tickets}\n"
                f"⏳ **Λήξη Κλήρωσης σε:** {hours} ώρες και {minutes} λεπτά!\n\n"
                f"🎁 **Τα Δώρα της Κλήρωσης:**\n{prizes_text}\n\n"
                f"💡 *Κάθε 10€ αγορών (50 πόντοι) σου εξασφαλίζουν αυτόματα 1 εισιτήριο!*"
            )
            
    await message.answer(text, parse_mode="Markdown")