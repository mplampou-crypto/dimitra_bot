import asyncio
import datetime
import logging
from aiogram import Bot, Dispatcher
from config import TOKEN, ADMIN_IDS
from database import init_db, get_db

# Import των routers που φτιάξαμε
from handlers.user import router as user_router
from handlers.wallet import router as wallet_router
from handlers.catalog import router as catalog_router
from handlers.cart import router as cart_router
from handlers.admin import router as admin_router

# --- BACKGROUND TASK ΓΙΑ ΕΛΕΓΧΟ ΛΗΞΗΣ ΣΥΝΔΡΟΜΩΝ ---
async def check_subscriptions_loop(bot: Bot):
    while True:
        try:
            db_pool = await get_db()
            if db_pool:
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

async def main():
    logging.basicConfig(level=logging.INFO)
    
    # 1. Εκκίνηση της Βάσης Δεδομένων
    await init_db()
    
    # 2. Ρύθμιση του Bot και του Dispatcher
    bot = Bot(token=TOKEN)
    dp = Dispatcher()
    
    # 3. Συνδεδεμένοι Routers (Η νέα αρθρωτή δομή)
    dp.include_router(user_router)
    dp.include_router(wallet_router)
    dp.include_router(catalog_router)
    dp.include_router(cart_router)
    dp.include_router(admin_router)
    
    # 4. Εκκίνηση Background Task για τις συνδρομές
    asyncio.create_task(check_subscriptions_loop(bot))
    
    print("Το Bot της Δήμητρας ξεκίνησε επιτυχώς με όλες τις νέες δυνατότητες!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())