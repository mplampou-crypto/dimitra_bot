import os

# --- ΒΑΣΙΚΕΣ ΡΥΘΜΙΣΕΙΣ ---
TOKEN = os.getenv("BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
DB_URL = os.getenv("DATABASE_URL", "postgresql://db_user:db_password@localhost:5432/db_name")

# Εδώ βάζεις το ID σου (ή και άλλων admins με κόμμα)
ADMIN_IDS = [int(admin_id.strip()) for admin_id in os.getenv("ADMIN_IDS", "123456789").split(",") if admin_id.strip()]

# Διεύθυνση Πορτοφολιού (ΜΟΝΟ για το MoonPay να σου στέλνει τα χρήματα)
BTC_WALLET = os.getenv("BTC_WALLET", "1waQSukZCthRAwc7D97hh918gG7CwXz2N")

# --- LINKS ---
GROUP_LINK = "https://t.me/+h9QI608rXMUxOWI0"
PREMIUM_GROUP_LINK = "https://t.me/+EXhF_sUBHZZlZGQ0" # Η συνδρομητική (VIP) ομάδα
ADMIN_LINK = "https://t.me/dimitrasavvidi"

# --- ΓΡΑΦΙΚΑ & ΕΙΚΟΝΕΣ ---
PENDING_GRAPHIC_URL = "https://dummyimage.com/600x400/1a1a1a/ffcc00&text=Please+Wait+For+Approval"