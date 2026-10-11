import asyncpg
from config import DB_URL

db_pool = None

async def init_db():
    global db_pool
    db_pool = await asyncpg.create_pool(DB_URL)
    async with db_pool.acquire() as connection:
        # 1. Χρήστες
        await connection.execute("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_id BIGINT PRIMARY KEY,
                balance NUMERIC(10, 2) DEFAULT 0.00,
                lifetime_points INT DEFAULT 0,
                giveaway_tickets INT DEFAULT 0,
                total_spent NUMERIC(10, 2) DEFAULT 0.00,
                instagram TEXT,
                tiktok TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            );
        """)
        
        # 2. Αγορές
        await connection.execute("""
            CREATE TABLE IF NOT EXISTS purchases (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT,
                items_summary TEXT,
                total_price NUMERIC(10, 2),
                created_at TIMESTAMP DEFAULT NOW()
            );
        """)
        
        # 3. Κατάλογος & Gallery
        await connection.execute("""
            CREATE TABLE IF NOT EXISTS locked_media (
                id SERIAL PRIMARY KEY,
                file_ids TEXT[] DEFAULT ARRAY[]::TEXT[],
                media_types TEXT[] DEFAULT ARRAY[]::TEXT[],
                description TEXT,
                price NUMERIC(10, 2) NOT NULL,
                category TEXT DEFAULT 'photo',
                is_gallery BOOLEAN DEFAULT FALSE,
                unlock_level INT DEFAULT 0,
                unlock_percentage INT DEFAULT 0,
                is_subscription BOOLEAN DEFAULT FALSE,
                duration_months INT DEFAULT 0,
                is_custom BOOLEAN DEFAULT FALSE
            );
        """)
        
        # 4. Ενεργές Συνδρομές
        await connection.execute("""
            CREATE TABLE IF NOT EXISTS active_subscriptions (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT UNIQUE,
                user_full_name TEXT,
                expires_at TIMESTAMP NOT NULL,
                notified_expiry BOOLEAN DEFAULT FALSE
            );
        """)
        
        # 5. Καλάθι
        await connection.execute("""
            CREATE TABLE IF NOT EXISTS cart (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT,
                media_id INT,
                added_at TIMESTAMP DEFAULT NOW()
            );
        """)
        
        # 6. Giveaway Settings
        await connection.execute("""
            CREATE TABLE IF NOT EXISTS giveaway_settings (
                id INT PRIMARY KEY,
                ends_at TIMESTAMP,
                prizes TEXT
            );
        """)

        # 7. Promo Codes
        await connection.execute("""
            CREATE TABLE IF NOT EXISTS promo_codes (
                code TEXT PRIMARY KEY,
                discount INT NOT NULL,
                bonus_tickets INT DEFAULT 0,
                min_level INT DEFAULT 0,
                expires_at TIMESTAMP
            );
        """)
        
        # 8. Χρήση Promo Codes
        await connection.execute("""
            CREATE TABLE IF NOT EXISTS user_promo_usage (
                telegram_id BIGINT,
                promo_code TEXT,
                used_at TIMESTAMP DEFAULT NOW(),
                PRIMARY KEY (telegram_id, promo_code)
            );
        """)
        
        # 9. Αιτήματα PaySafe
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

        # 10. Αιτήματα MoonPay
        await connection.execute("""
            CREATE TABLE IF NOT EXISTS moonpay_requests (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT,
                amount NUMERIC(10, 2),
                photo_file_id TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT NOW()
            );
        """)

        # --- DATABASE UPGRADE SCRIPT ---
        # Τρέχει ΠΡΙΝ τα INSERT για να βεβαιωθούμε ότι οι νέες στήλες υπάρχουν!
        try:
            await connection.execute("ALTER TABLE locked_media ADD COLUMN IF NOT EXISTS category TEXT DEFAULT 'photo';")
            await connection.execute("ALTER TABLE locked_media ADD COLUMN IF NOT EXISTS is_gallery BOOLEAN DEFAULT FALSE;")
            await connection.execute("ALTER TABLE locked_media ADD COLUMN IF NOT EXISTS unlock_level INT DEFAULT 0;")
            await connection.execute("ALTER TABLE locked_media ADD COLUMN IF NOT EXISTS unlock_percentage INT DEFAULT 0;")
            await connection.execute("ALTER TABLE promo_codes ADD COLUMN IF NOT EXISTS min_level INT DEFAULT 0;")
            await connection.execute("ALTER TABLE giveaway_settings ADD COLUMN IF NOT EXISTS prizes TEXT;")
        except Exception as e:
            print(f"Σημείωση Βάσης Δεδομένων (Αναμενόμενο αν είναι ήδη ενημερωμένη): {e}")

        # Τώρα που η στήλη 'prizes' έχει σίγουρα προστεθεί, κάνουμε το INSERT με ασφάλεια.
        await connection.execute("""
            INSERT INTO giveaway_settings (id, ends_at, prizes) 
            VALUES (1, '2000-01-01 00:00:00', '1. Τα δώρα θα ανακοινωθούν σύντομα!') 
            ON CONFLICT (id) DO NOTHING;
        """)

async def get_db():
    return db_pool